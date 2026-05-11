"""Wrapper OPUS-MT GPU (Helsinki-NLP) — traduction rapide multi-paires.

Licence Apache 2.0 → OK pour usage commercial.
Paires : FR↔EN, FR↔DE, FR↔ES, FR↔IT.

Optimisation : CTranslate2 (~3× plus rapide que MarianMT pur). Conversion
au premier appel, mise en cache dans HF_CACHE/ct2/.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
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
        # Le backend EFFECTIF est stocké dans le bundle (pas self.use_ct2)
        # car un fallback peut basculer ct2 → transformers pour UNE paire
        # sans affecter les autres (cf. _load_ct2).
        if bundle.get("backend") == "ct2":
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
        """Charge un modèle OPUS-MT en CTranslate2.

        Stratégie :
        1. Si le dossier ct2_dir contient déjà ``model.bin`` → reload direct
        2. Sinon : tente la conversion via ``ct2-transformers-converter``
           (subprocess pour capturer stderr proprement)
        3. Si la conversion plante (binaire absent, modèle HF down, etc.)
           → cleanup du dossier partiel + fallback sur transformers backend
           (3× plus lent mais marche sans CTranslate2)
        """
        import ctranslate2
        from transformers import MarianTokenizer

        cache_root = Path(HF_CACHE) / "ct2"
        cache_root.mkdir(parents=True, exist_ok=True)
        ct2_dir = cache_root / model_name.replace("/", "_")
        model_bin = ct2_dir / "model.bin"

        # Vérification PRÉCISE (model.bin, pas juste le dossier — la
        # conversion peut avoir créé un dossier vide en cas d'échec).
        if not model_bin.exists():
            # Nettoyage d'un éventuel dossier partiel/incomplet
            if ct2_dir.exists():
                log.warning("CTranslate2 dir exists but model.bin missing — "
                            "cleaning up before re-conversion: %s", ct2_dir)
                shutil.rmtree(ct2_dir, ignore_errors=True)

            log.info("Converting %s to CTranslate2 (one-shot, INT8)…",
                     model_name)
            cmd = [
                "ct2-transformers-converter",
                "--model", model_name,
                "--output_dir", str(ct2_dir),
                "--copy_files", "generation_config.json",
                "tokenizer_config.json", "source.spm", "target.spm", "vocab.json",
                "--quantization", "int8",
                "--force",
            ]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300,
                )
                if result.returncode != 0:
                    log.error("ct2-transformers-converter failed (rc=%d):\n"
                              "STDOUT:\n%s\nSTDERR:\n%s",
                              result.returncode,
                              result.stdout[-2000:],
                              result.stderr[-2000:])
                    # Cleanup avant fallback
                    shutil.rmtree(ct2_dir, ignore_errors=True)
                    raise RuntimeError(
                        f"CTranslate2 conversion failed for {model_name}")
            except FileNotFoundError as exc:
                # Le binaire ct2-transformers-converter n'est pas installé
                log.error("ct2-transformers-converter binary not found: %s. "
                          "Falling back to transformers backend.", exc)
                self.use_ct2 = False
                return self._load_transformers(model_name)
            except (subprocess.TimeoutExpired, RuntimeError) as exc:
                log.error("CTranslate2 conversion failed (%s) — "
                          "falling back to transformers backend.", exc)
                self.use_ct2 = False
                return self._load_transformers(model_name)

        # Vérification finale avant ouverture (sécurité, si la conversion
        # a passé sans erreur mais sans produire model.bin)
        if not model_bin.exists():
            log.error("CTranslate2 conversion succeeded (rc=0) but "
                      "model.bin still missing in %s — fallback transformers",
                      ct2_dir)
            self.use_ct2 = False
            return self._load_transformers(model_name)

        translator = ctranslate2.Translator(str(ct2_dir), device="cuda")
        tok = MarianTokenizer.from_pretrained(model_name, cache_dir=HF_CACHE)
        log.info("OPUS-MT %s loaded via CTranslate2 INT8", model_name)
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
