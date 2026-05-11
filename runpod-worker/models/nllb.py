"""Wrapper NLLB-200 distilled 1.3B pour traduction multilingue (200+ langues).

Variante distillée 1.3B = meilleur compromis qualité/perf (vs 600M plus rapide
mais moins bon, et 3.3B trop lourd). Quantization INT8 si bitsandbytes dispo.

Cf. Décision 6 du document 00-decisions-v3.md : NLLB conservé tel quel,
risque licence CC-BY-NC connu et accepté.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("voicebridge.nllb")

MODEL_ID = "facebook/nllb-200-distilled-1.3B"
HF_CACHE = os.environ.get("HF_HOME", "/runpod-volume/hf-cache")


# Mapping codes ISO simplifiés → codes NLLB (avec script Latn/Cyrl/etc.)
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
    """Wrapper NLLB-200 distilled 1.3B."""

    def __init__(self):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        import torch

        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, cache_dir=HF_CACHE)

        # /!\ Volume RunPod a UNIQUEMENT model.safetensors (pas pytorch_model.bin
        # — supprimé pour économiser ~5 Go). bitsandbytes INT8 exige
        # pytorch_model.bin, donc on ne tente PAS l'INT8 ici.
        # FP16 sur RTX 4090 = ~5 Go VRAM, largement OK.
        #
        # use_safetensors=True : force transformers à utiliser le snapshot
        # safetensors et à NE PAS retomber sur un download HF (sinon ça
        # plante avec "Disk quota exceeded" car le Volume est saturé).
        # local_files_only=True : interdit toute tentative de download.
        try:
            self.model = AutoModelForSeq2SeqLM.from_pretrained(
                MODEL_ID,
                cache_dir=HF_CACHE,
                torch_dtype=torch.float16,
                use_safetensors=True,
                local_files_only=True,
            ).to("cuda")
            log.info("NLLB loaded (FP16 safetensors, offline)")
        except Exception as e:
            # Fallback : retente sans local_files_only au cas où le cache
            # n'aurait pas le snapshot config.json référencé. Si ça plante
            # aussi → le Volume est mal monté.
            log.warning("NLLB offline load failed (%s), retry online", e)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(
                MODEL_ID,
                cache_dir=HF_CACHE,
                torch_dtype=torch.float16,
                use_safetensors=True,
            ).to("cuda")
            log.info("NLLB loaded (FP16 safetensors, online fallback)")

        self.model.eval()

    def translate(self, text: str, src: str, tgt: str) -> str:
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

        self.tokenizer.src_lang = src_code
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True,
                                max_length=512).to("cuda")

        forced_bos = self.tokenizer.convert_tokens_to_ids(tgt_code)

        import torch
        with torch.no_grad():
            generated = self.model.generate(
                **inputs,
                forced_bos_token_id=forced_bos,
                max_length=512,
                num_beams=1,    # rapide ; passer à 4 pour qualité max
            )

        return self.tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
