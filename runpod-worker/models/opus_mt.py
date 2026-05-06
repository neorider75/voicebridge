"""Wrapper OPUS-MT GPU (Helsinki-NLP) — traduction rapide multi-paires.

Licence Apache 2.0 → OK pour usage commercial.
Paires : FR↔EN, FR↔DE, FR↔ES, FR↔IT.

Optimisation : CTranslate2 (~3× plus rapide que MarianMT pur). Conversion
au premier appel, mise en cache dans HF_CACHE/ct2/.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("voicebridge.opus_mt")

HF_CACHE = os.environ.get("HF_HOME", "/runpod-volume/hf-cache")

MODEL_NAMES: dict[tuple[str, str], str] = {
    ("fr", "en"): "Helsinki-NLP/opus-mt-fr-en",
    ("en", "fr"): "Helsinki-NLP/opus-mt-en-fr",
    ("fr", "de"): "Helsinki-NLP/opus-mt-fr-de",
    ("de", "fr"): "Helsinki-NLP/opus-mt-de-fr",
    ("fr", "es"): "Helsinki-NLP/opus-mt-fr-es",
    ("es", "fr"): "Helsinki-NLP/opus-mt-es-fr",
    ("fr", "it"): "Helsinki-NLP/opus-mt-fr-it",
    ("it", "fr"): "Helsinki-NLP/opus-mt-it-fr",
}


class OpusMT:
    """Wrapper unifié OPUS-MT GPU. Charge les modèles à la demande (LRU max 4)."""

    def __init__(self, max_cached_models: int = 4):
        self.max_cached = max_cached_models
        self._cache: dict[tuple[str, str], dict] = {}
        try:
            import ctranslate2  # noqa: F401
            self.use_ct2 = True
            log.info("OPUS-MT will use CTranslate2 backend")
        except ImportError:
            self.use_ct2 = False
            log.info("OPUS-MT will use transformers backend (fallback)")

    def supports(self, src: str, tgt: str) -> bool:
        return (src, tgt) in MODEL_NAMES

    def translate(self, text: str, src: str, tgt: str) -> str:
        if not text or not text.strip():
            return text
        if src == tgt:
            return text
        if not self.supports(src, tgt):
            raise ValueError(f"OPUS-MT: paire non supportée {src}→{tgt}")

        bundle = self._load(src, tgt)
        if self.use_ct2:
            return self._translate_ct2(text, bundle)
        return self._translate_transformers(text, bundle)

    def _load(self, src: str, tgt: str) -> dict:
        key = (src, tgt)
        if key in self._cache:
            return self._cache[key]

        if len(self._cache) >= self.max_cached:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
            log.info("OPUS-MT cache eviction: %s", oldest)

        model_name = MODEL_NAMES[key]
        log.info("Loading OPUS-MT %s ...", model_name)

        if self.use_ct2:
            bundle = self._load_ct2(model_name)
        else:
            bundle = self._load_transformers(model_name)

        self._cache[key] = bundle
        return bundle

    def _load_transformers(self, model_name: str) -> dict:
        from transformers import MarianMTModel, MarianTokenizer
        import torch
        tok = MarianTokenizer.from_pretrained(model_name, cache_dir=HF_CACHE)
        model = MarianMTModel.from_pretrained(
            model_name, cache_dir=HF_CACHE, torch_dtype=torch.float16
        ).to("cuda")
        model.eval()
        return {"tokenizer": tok, "model": model, "backend": "transformers"}

    def _load_ct2(self, model_name: str) -> dict:
        import ctranslate2
        from transformers import MarianTokenizer

        cache_root = Path(HF_CACHE) / "ct2"
        cache_root.mkdir(parents=True, exist_ok=True)
        ct2_dir = cache_root / model_name.replace("/", "_")

        if not ct2_dir.exists():
            log.info("Converting %s to CTranslate2 (one-shot)", model_name)
            os.system(
                f"ct2-transformers-converter "
                f"--model {model_name} "
                f"--output_dir {ct2_dir} "
                f"--copy_files generation_config.json tokenizer_config.json source.spm target.spm vocab.json "
                f"--quantization int8 "
                f"--force"
            )

        translator = ctranslate2.Translator(str(ct2_dir), device="cuda")
        tok = MarianTokenizer.from_pretrained(model_name, cache_dir=HF_CACHE)
        return {"tokenizer": tok, "translator": translator, "backend": "ct2"}

    def _translate_transformers(self, text: str, bundle: dict) -> str:
        import torch
        tok = bundle["tokenizer"]
        model = bundle["model"]
        inputs = tok(
            [text], return_tensors="pt", padding=True, truncation=True, max_length=512
        ).to("cuda")
        with torch.no_grad():
            generated = model.generate(**inputs, num_beams=1, max_length=512)
        return tok.batch_decode(generated, skip_special_tokens=True)[0].strip()

    def _translate_ct2(self, text: str, bundle: dict) -> str:
        tok = bundle["tokenizer"]
        translator = bundle["translator"]
        tokens = tok.tokenize(text)
        results = translator.translate_batch(
            [tokens], beam_size=1, max_decoding_length=512
        )
        translated_tokens = results[0].hypotheses[0]
        return tok.convert_tokens_to_string(translated_tokens).strip()
