"""Service de traduction offline — Helsinki-NLP OPUS-MT via HuggingFace transformers.

Utilisé par le pipeline Live pour la traduction en temps réel (FR↔EN).
Les modèles (~300 Mo par paire) sont téléchargés depuis HuggingFace au premier
appel et mis en cache dans ``data/models/translation/``.

V1 supporte uniquement fr↔en (les deux langues V1 de VoiceBridge).
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("voicebridge.translation")

# Cache en RAM : (src, tgt) → (tokenizer, model)
_models: dict[tuple[str, str], tuple] = {}

# Mapping (src, tgt) → nom de modèle HuggingFace.
# On préfère les modèles "tc-big" (meilleure qualité) quand ils existent.
_MODEL_NAMES: dict[tuple[str, str], str] = {
    ("fr", "en"): "Helsinki-NLP/opus-mt-fr-en",
    ("en", "fr"): "Helsinki-NLP/opus-mt-en-fr",
}


class TranslationError(Exception):
    pass


def _load(src: str, tgt: str) -> tuple:
    """Charge (ou retourne depuis le cache) le tokenizer + modèle MarianMT."""
    key = (src, tgt)
    if key in _models:
        return _models[key]

    model_name = _MODEL_NAMES.get(key)
    if not model_name:
        raise TranslationError(f"Paire de langues non supportée : {src}→{tgt}. "
                               f"V1 supporte uniquement fr↔en.")

    try:
        from transformers import MarianMTModel, MarianTokenizer  # type: ignore
    except ImportError as exc:
        raise TranslationError(f"transformers non installé : {exc}") from exc

    try:
        from .. import config
        cache_dir = config.MODELS_DIR / "translation"
        cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 — si config absent (tests unitaires)
        cache_dir = None  # type: ignore

    log.info("translation: chargement %s …", model_name)
    try:
        tok = MarianTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
        mdl = MarianMTModel.from_pretrained(model_name, cache_dir=cache_dir)
    except Exception as exc:  # noqa: BLE001
        raise TranslationError(f"Impossible de charger {model_name} : {exc}") from exc

    mdl.eval()
    _models[key] = (tok, mdl)
    log.info("translation: modèle %s chargé", model_name)
    return _models[key]


def translate(text: str, src_lang: str, tgt_lang: str) -> str:
    """Traduit ``text`` de ``src_lang`` vers ``tgt_lang``.

    Synchrone / thread-safe (modèle en mode eval, pas d'état mutable).
    Pour l'utiliser sans bloquer l'event loop asyncio, appelez-le depuis un
    thread (ex: ``await asyncio.to_thread(translate, text, src, tgt)``).

    Raises:
        TranslationError: si la paire de langues n'est pas supportée ou si le
            modèle ne peut pas être chargé.
    """
    if not text or not text.strip():
        return text
    if src_lang == tgt_lang:
        return text

    try:
        import torch  # type: ignore
    except ImportError as exc:
        raise TranslationError(f"torch non disponible : {exc}") from exc

    tok, mdl = _load(src_lang, tgt_lang)

    inputs = tok(
        [text],
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=512,
    )
    with torch.no_grad():
        generated = mdl.generate(**inputs, num_beams=4, early_stopping=True)
    result = tok.batch_decode(generated, skip_special_tokens=True)[0]
    log.debug("translation: %r → %r", text[:60], result[:60])
    return result


def unload() -> None:
    """Libère tous les modèles du cache RAM (appelé par le déchargement global)."""
    _models.clear()
    log.info("translation: modèles déchargés")
