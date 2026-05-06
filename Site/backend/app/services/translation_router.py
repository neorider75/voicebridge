"""Routeur multi-providers de traduction.

Dispatch un appel ``translate()`` vers le bon backend selon le provider
sélectionné par l'utilisateur :

| Provider          | Backend                            | Coût      |
|-------------------|------------------------------------|-----------|
| ``opus-mt-cpu``   | services/translation.py (V1, CPU)  | gratuit   |
| ``opus-mt-gpu``   | RunPod worker (operation=translate)| ~0.001€/s |
| ``nllb``          | RunPod worker (operation=translate)| ~0.002€/s |
| ``gpt-4o-mini``   | OpenAI Chat Completions            | ~0.0004€  |
| ``gpt-4o``        | OpenAI Chat Completions            | ~0.005€   |

Le routeur expose une API uniforme. Pour les cas qui demandent du contexte
(briefings GPT, mémoire conversationnelle), passer par
``openai_client.TranslationSession`` directement.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .. import config
from . import openai_client, runpod_client, translation as opus_mt_cpu

log = logging.getLogger("voicebridge.translation_router")


SUPPORTED_PROVIDERS = (
    "opus-mt-cpu",
    "opus-mt-gpu",
    "nllb",
    "gpt-4o-mini",
    "gpt-4o",
)


@dataclass
class RouterResult:
    translated: str
    provider: str
    src: str
    tgt: str
    latency_ms: int
    cost_eur: float = 0.0


def list_providers() -> list[dict]:
    """Liste les providers disponibles avec leur statut.

    Utilisé par ``GET /api/cloud/providers`` pour peupler la UI.
    """
    return [
        {
            "id": "opus-mt-cpu",
            "name": "OPUS-MT (CPU local)",
            "languages": "FR↔EN",
            "latency_ms": 80,
            "cost_per_phrase_eur": 0.0,
            "available": True,
            "default_for": ["fr-en", "en-fr"],
        },
        {
            "id": "opus-mt-gpu",
            "name": "OPUS-MT (GPU RunPod)",
            "languages": "FR↔EN/DE/ES/IT",
            "latency_ms": 80,
            "cost_per_phrase_eur": 0.001,
            "available": runpod_client.is_configured(),
        },
        {
            "id": "nllb",
            "name": "NLLB-200 (GPU RunPod)",
            "languages": "200+ langues",
            "latency_ms": 150,
            "cost_per_phrase_eur": 0.002,
            "available": runpod_client.is_configured(),
            "license_note": "CC-BY-NC",
        },
        {
            "id": "gpt-4o-mini",
            "name": "GPT-4o-mini (OpenAI)",
            "languages": "Universel",
            "latency_ms": 600,
            "cost_per_phrase_eur": 0.0004,
            "available": openai_client.is_configured(),
            "supports_context": True,
        },
        {
            "id": "gpt-4o",
            "name": "GPT-4o (OpenAI)",
            "languages": "Universel — qualité top",
            "latency_ms": 1100,
            "cost_per_phrase_eur": 0.005,
            "available": openai_client.is_configured(),
            "supports_context": True,
        },
    ]


def get_default_provider() -> str:
    """Provider par défaut depuis config (fallback opus-mt-cpu)."""
    val = config.get("default_translation_provider", "opus-mt-cpu")
    if val not in SUPPORTED_PROVIDERS:
        log.warning("default_translation_provider invalide (%s), fallback opus-mt-cpu", val)
        return "opus-mt-cpu"
    return val


def translate(
    text: str,
    src: str,
    tgt: str,
    provider: Optional[str] = None,
    briefing: str = "",
    glossary: Optional[dict[str, str]] = None,
    fallback: bool = True,
) -> RouterResult:
    """Traduit ``text`` via le provider sélectionné.

    Args:
        text: texte source
        src: langue source (code ISO)
        tgt: langue cible (code ISO)
        provider: ``opus-mt-cpu`` | ``opus-mt-gpu`` | ``nllb`` |
                  ``gpt-4o-mini`` | ``gpt-4o``. None → défaut config.
        briefing: contexte de session (GPT seulement, ignoré pour autres)
        glossary: mapping FR→EN (GPT seulement)
        fallback: si True, retombe sur opus-mt-cpu si le provider demandé
                  échoue ou n'est pas disponible.

    Returns:
        ``RouterResult`` avec ``translated``, ``latency_ms``, ``cost_eur``.
    """
    if not text or not text.strip():
        return RouterResult(text, provider or "noop", src, tgt, 0)
    if src == tgt:
        return RouterResult(text, provider or "noop", src, tgt, 0)

    provider = provider or get_default_provider()
    if provider not in SUPPORTED_PROVIDERS:
        if fallback:
            log.warning("Provider inconnu %s, fallback opus-mt-cpu", provider)
            provider = "opus-mt-cpu"
        else:
            raise ValueError(f"Provider non supporté : {provider}")

    try:
        return _dispatch(text, src, tgt, provider, briefing, glossary)
    except Exception as exc:  # noqa: BLE001
        if not fallback or provider == "opus-mt-cpu":
            raise
        log.warning("Provider %s a échoué (%s), fallback opus-mt-cpu", provider, exc)
        return _dispatch(text, src, tgt, "opus-mt-cpu", briefing, glossary)


def _dispatch(text: str, src: str, tgt: str, provider: str,
              briefing: str, glossary: Optional[dict[str, str]]) -> RouterResult:
    if provider == "opus-mt-cpu":
        t0 = time.time()
        translated = opus_mt_cpu.translate(text, src, tgt)
        return RouterResult(
            translated=translated,
            provider="opus-mt-cpu",
            src=src, tgt=tgt,
            latency_ms=int((time.time() - t0) * 1000),
            cost_eur=0.0,
        )

    if provider in ("opus-mt-gpu", "nllb"):
        # Provider GPU côté worker RunPod
        t0 = time.time()
        worker_provider = "opus-mt" if provider == "opus-mt-gpu" else "nllb"
        out = runpod_client.runsync({
            "operation": "translate",
            "provider": worker_provider,
            "text": text,
            "src_lang": src,
            "tgt_lang": tgt,
        })
        return RouterResult(
            translated=out.get("translated", text),
            provider=provider,
            src=src, tgt=tgt,
            latency_ms=int((time.time() - t0) * 1000),
            cost_eur=0.0,   # facturation GPU calculée au niveau session, pas par phrase
        )

    if provider in ("gpt-4o-mini", "gpt-4o"):
        result = openai_client.translate_one_shot(
            text=text, src=src, tgt=tgt, provider=provider,
            briefing=briefing, glossary=glossary,
        )
        return RouterResult(
            translated=result.translated,
            provider=provider,
            src=src, tgt=tgt,
            latency_ms=result.latency_ms,
            cost_eur=result.cost_eur,
        )

    raise ValueError(f"Dispatch non implémenté pour {provider}")
