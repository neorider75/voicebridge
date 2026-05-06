"""Client OpenAI pour traduction GPT-4o(-mini) avec contexte conversationnel.

Cf. Décision 7 du document ``00-decisions-v3.md`` : trois niveaux de contexte
sont injectés dans le prompt système :
- **Glossaire métier** (permanent, ``config.translation_glossary``)
- **Briefing de session** (sélectionné par l'utilisateur dans le studio Live)
- **Mémoire conversationnelle** (N dernières phrases, automatique côté backend)

Configuration attendue dans ``config.json`` :

    {
      "openai_api_key_encrypted": "gAAAAA...",
      "translation_glossary": {"CODIR": "Executive Committee", ...},
      "translation_history_size": 5
    }

Usage typique :

    from ..services import openai_client
    session = openai_client.TranslationSession(
        provider="gpt-4o-mini",
        briefing="Réunion CODIR Limagrain, marges Q1...",
    )
    result = session.translate("Bonjour le monde", src="fr", tgt="en")
    # result.translated, result.cost_eur, result.latency_ms
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from .. import config
from . import secrets

log = logging.getLogger("voicebridge.openai")

# Tarifs OpenAI (USD/1M tokens, mai 2026 — à ajuster si évolution tarifs)
# Source : https://openai.com/api/pricing/
PRICING_USD_PER_1M = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o":      {"input": 2.50, "output": 10.00},
}
USD_TO_EUR = 0.93   # Approximation, à ajuster — non critique (affichage UI seulement)


class OpenAIError(Exception):
    """Erreur générique d'appel OpenAI (réseau, auth, business)."""


class OpenAINotConfiguredError(OpenAIError):
    """Clé OpenAI absente dans config.json."""


# ────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────


def is_configured() -> bool:
    return bool(config.get("openai_api_key_encrypted"))


def get_api_key() -> str:
    enc = config.get("openai_api_key_encrypted", "")
    if not enc:
        raise OpenAINotConfiguredError(
            "openai_api_key_encrypted absent — saisir la clé dans Settings → Cloud"
        )
    return secrets.decrypt(enc)


def _get_client():
    """Lazy import + singleton du client OpenAI."""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise OpenAIError(
            f"openai non installé : {exc}. pip install 'openai>=1.50'"
        ) from exc
    return OpenAI(api_key=get_api_key())


# ────────────────────────────────────────────────────────────────────
# Construction du prompt
# ────────────────────────────────────────────────────────────────────


_LANG_NAMES = {
    "fr": "French", "en": "English", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ru": "Russian",
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "ar": "Arabic",
}


def _format_glossary(glossary: dict[str, str]) -> str:
    if not glossary:
        return ""
    lines = [f'  - "{src}" → "{tgt}"' for src, tgt in glossary.items()]
    return "Glossary (apply systematically):\n" + "\n".join(lines)


def _build_system_prompt(src: str, tgt: str, briefing: str = "",
                         glossary: Optional[dict[str, str]] = None) -> str:
    src_name = _LANG_NAMES.get(src, src)
    tgt_name = _LANG_NAMES.get(tgt, tgt)
    parts = [
        f"You are a professional simultaneous interpreter translating from "
        f"{src_name} to {tgt_name}.",
        "Output ONLY the translated text — no preamble, no quotes, no notes, "
        "no explanations.",
        "Preserve idioms naturally. Resolve ambiguous pronouns using the "
        "conversation history below.",
        "Keep the same tone, register, and punctuation style.",
    ]
    if briefing:
        parts.append(f"\nSession context:\n{briefing.strip()}")
    if glossary:
        parts.append(f"\n{_format_glossary(glossary)}")
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────────
# Session de traduction (mémoire conversationnelle)
# ────────────────────────────────────────────────────────────────────


@dataclass
class TranslationResult:
    translated: str
    provider: str
    src: str
    tgt: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_eur: float


class TranslationSession:
    """Session de traduction GPT avec mémoire conversationnelle.

    Garde les N dernières paires (source, traduction) pour fournir le contexte
    aux phrases suivantes (résolution des pronoms, cohérence terminologique).

    Une instance correspond typiquement à UNE session Live côté backend.
    À jeter à la fin de la session (le buffer ne persiste pas sur disque).
    """

    def __init__(
        self,
        provider: str = "gpt-4o-mini",
        briefing: str = "",
        glossary: Optional[dict[str, str]] = None,
        history_size: Optional[int] = None,
    ):
        if provider not in PRICING_USD_PER_1M:
            raise OpenAIError(
                f"Provider {provider!r} inconnu. "
                f"Supportés : {list(PRICING_USD_PER_1M.keys())}"
            )
        self.provider = provider
        self.briefing = briefing
        self.glossary = glossary or config.get("translation_glossary", {}) or {}
        max_size = history_size or int(config.get("translation_history_size", 5))
        self.history: deque[tuple[str, str]] = deque(maxlen=max_size)
        self.total_cost_eur = 0.0

    def _build_messages(self, text: str, src: str, tgt: str) -> list[dict]:
        sys_prompt = _build_system_prompt(src, tgt, self.briefing, self.glossary)
        msgs = [{"role": "system", "content": sys_prompt}]

        # Mémoire conversationnelle : N dernières paires source/traduction
        for prev_src, prev_tgt in self.history:
            msgs.append({"role": "user", "content": prev_src})
            msgs.append({"role": "assistant", "content": prev_tgt})

        msgs.append({"role": "user", "content": text})
        return msgs

    def translate(self, text: str, src: str, tgt: str) -> TranslationResult:
        if not text or not text.strip():
            return TranslationResult(text, self.provider, src, tgt, 0, 0, 0, 0.0)
        if src == tgt:
            return TranslationResult(text, self.provider, src, tgt, 0, 0, 0, 0.0)

        client = _get_client()
        messages = self._build_messages(text, src, tgt)

        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=self.provider,
                messages=messages,
                temperature=0.1,    # Déterminisme pour traduction
                max_tokens=512,
            )
        except Exception as exc:  # noqa: BLE001
            raise OpenAIError(f"OpenAI chat.completions failed: {exc}") from exc

        latency_ms = int((time.time() - t0) * 1000)
        translated = (resp.choices[0].message.content or "").strip()

        usage = resp.usage
        in_tok = usage.prompt_tokens if usage else 0
        out_tok = usage.completion_tokens if usage else 0
        cost_eur = self._cost(in_tok, out_tok)
        self.total_cost_eur += cost_eur

        # Update mémoire conversationnelle
        self.history.append((text, translated))

        log.info("openai translate (%s) %dms in=%d out=%d cost=%.5f€ → %r",
                 self.provider, latency_ms, in_tok, out_tok, cost_eur,
                 translated[:60])

        return TranslationResult(
            translated=translated,
            provider=self.provider,
            src=src,
            tgt=tgt,
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_eur=cost_eur,
        )

    def _cost(self, in_tokens: int, out_tokens: int) -> float:
        rates = PRICING_USD_PER_1M.get(self.provider, {})
        usd = (in_tokens / 1_000_000.0) * rates.get("input", 0)
        usd += (out_tokens / 1_000_000.0) * rates.get("output", 0)
        return usd * USD_TO_EUR


# ────────────────────────────────────────────────────────────────────
# One-shot (sans mémoire) — utilisé pour `/api/translate` synchrone
# ────────────────────────────────────────────────────────────────────


def translate_one_shot(
    text: str,
    src: str,
    tgt: str,
    provider: str = "gpt-4o-mini",
    briefing: str = "",
    glossary: Optional[dict[str, str]] = None,
) -> TranslationResult:
    """Traduction unique sans mémoire conversationnelle (cas REST one-shot)."""
    session = TranslationSession(provider=provider, briefing=briefing,
                                  glossary=glossary, history_size=0)
    return session.translate(text, src, tgt)


# ────────────────────────────────────────────────────────────────────
# Health / status
# ────────────────────────────────────────────────────────────────────


def ping() -> dict:
    """Vérifie que la clé OpenAI est valide. Appel léger : ``models.list``."""
    t0 = time.time()
    try:
        client = _get_client()
        models = client.models.list()
        # Confirme qu'au moins un modèle est listé
        list(models)
        return {
            "ok": True,
            "latency_ms": int((time.time() - t0) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        raise OpenAIError(f"OpenAI ping failed: {exc}") from exc
