"""Wrapper NLLB-200 distilled 1.3B pour traduction multilingue.

NLLB (No Language Left Behind, Meta) supporte 200+ langues.
Licence CC-BY-NC 4.0 → OK pour usage personnel, à valider pour usage commercial.

Variante distillée 1.3B : meilleur compromis qualité/perf.
- 600M : plus rapide mais qualité moindre
- 3.3B : qualité top mais lourd (17 Go)
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("voicebridge.nllb")

MODEL_ID = "facebook/nllb-200-distilled-1.3B"
HF_CACHE = os.environ.get("HF_HOME", "/runpod-volume/hf-cache")


# Mapping codes ISO simplifiés → codes NLLB (avec script)
NLLB_LANG_CODES = {
    "fr": "fra_Latn", "en": "eng_Latn", "de": "deu_Latn",
    "es": "spa_Latn", "it": "ita_Latn", "pt": "por_Latn",
    "nl": "nld_Latn", "ru": "rus_Cyrl", "uk": "ukr_Cyrl",
    "pl": "pol_Latn", "tr": "tur_Latn",
    "ja": "jpn_Jpan", "zh": "zho_Hans", "ko": "kor_Hang",
    "ar": "arb_Arab", "he": "heb_Hebr",
    "hi": "hin_Deva", "bn": "ben_Beng",
    "vi": "vie_Latn", "th": "tha_Thai", "id": "ind_Latn",
    "el": "ell_Grek", "ro": "ron_Latn", "cs": "ces_Latn",
    "sv": "swe_Latn", "no": "nob_Latn", "da": "dan_Latn",
    "fi": "fin_Latn", "hu": "hun_Latn",
}


class NLLB:
    """Wrapper NLLB-200 distilled 1.3B avec quantization INT8."""
    
    def __init__(self):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, BitsAndBytesConfig
        import torch
        
        self.tokenizer = AutoTokenizer.from_pretrained(
            MODEL_ID, cache_dir=HF_CACHE
        )
        
        # Quantization INT8 pour économiser VRAM et accélérer
        try:
            bnb_config = BitsAndBytesConfig(load_in_8bit=True)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(
                MODEL_ID,
                cache_dir=HF_CACHE,
                quantization_config=bnb_config,
            )
            log.info("NLLB loaded with INT8 quantization")
        except Exception as e:
            log.warning("INT8 quantization failed, fallback to FP16: %s", e)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(
                MODEL_ID,
                cache_dir=HF_CACHE,
                torch_dtype=torch.float16,
            ).to("cuda")
        
        self.model.eval()
    
    def translate(self, text: str, src: str, tgt: str) -> str:
        """Traduit text de src vers tgt.
        
        Args:
            text: texte à traduire
            src: code ISO source (fr, en, ...)
            tgt: code ISO cible
        """
        if not text or not text.strip():
            return text
        if src == tgt:
            return text
        
        src_code = NLLB_LANG_CODES.get(src)
        tgt_code = NLLB_LANG_CODES.get(tgt)
        if not src_code:
            raise ValueError(f"NLLB: langue source non supportée: {src}")
        if not tgt_code:
            raise ValueError(f"NLLB: langue cible non supportée: {tgt}")
        
        # Set source language for tokenizer
        self.tokenizer.src_lang = src_code
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True,
                                  max_length=512).to("cuda")
        
        # Force le BOS token de la langue cible
        forced_bos = self.tokenizer.convert_tokens_to_ids(tgt_code)
        
        import torch
        with torch.no_grad():
            generated = self.model.generate(
                **inputs,
                forced_bos_token_id=forced_bos,
                max_length=512,
                num_beams=1,  # rapide ; passer à 4 pour qualité max
            )
        
        result = self.tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
        return result.strip()
