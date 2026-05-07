"""Wrapper RVC (Retrieval-based Voice Conversion).

Charge dynamiquement les modèles .pth utilisateur depuis le RunPod Network
Volume. Cache LRU des derniers modèles utilisés (max 3 en VRAM).

Architecture :
- RVCRouter : gère le cache et le chargement des modèles depuis Volume
- RVCModel : un .pth chargé, expose convert() et convert_streaming()

Workflow utilisateur :
1. Modèle entraîné sur Kaggle par l'utilisateur (gratuit, ~3-6h)
2. .pth + .index uploadés depuis VoiceBridge sur RunPod Volume
   (via API S3 RunPod, cf. Décision 1 du doc 00-decisions-v3.md)
3. Worker charge à la demande, met en cache LRU
4. Inférence : audio source (F5-TTS native ou autre) → ta voix
"""
from __future__ import annotations

import base64
import io
import logging
import os
import time
from pathlib import Path
from typing import Generator

import numpy as np
import soundfile as sf

log = logging.getLogger("voicebridge.rvc")

RVC_VOLUME_PATH = os.environ.get("RVC_VOLUME_PATH", "/runpod-volume/rvc_models")
RVC_ASSETS_PATH = "/runpod-volume/rvc_assets"
HF_CACHE = os.environ.get("HF_HOME", "/runpod-volume/hf-cache")

# Hubert via transformers (facebook/hubert-base-ls960). Téléchargé une fois
# dans HF_HOME, ~360 Mo. Layer 12 pour RVC v2 (équivalent fairseq output_layer=12).
HUBERT_MODEL_ID = "facebook/hubert-base-ls960"
HUBERT_OUTPUT_LAYER = 12   # RVC v2 (utiliser 9 pour RVC v1 si besoin)

# Streaming chunk en samples (24000 sample_rate × 0.2s = 4800 samples)
STREAM_CHUNK_SAMPLES = 4800


class RVCRouter:
    """Cache LRU des modèles RVC chargés en VRAM."""

    def __init__(self, cache_size: int = 3):
        self.cache_size = cache_size
        self._cache: dict[str, tuple["RVCModel", float]] = {}
        self._init_shared_models()

    def _init_shared_models(self):
        """Charge hubert (via transformers) + rmvpe, partagés entre tous les RVCModel.

        Hubert : facebook/hubert-base-ls960 via transformers.HubertModel.
        Téléchargé automatiquement dans HF_HOME au 1er appel (~360 Mo).
        Note : remplace l'ancien hubert_base.pt + fairseq (retiré pour conflit
        hydra-core avec f5-tts).
        """
        rmvpe_path = os.path.join(RVC_ASSETS_PATH, "rmvpe.pt")

        try:
            from transformers import HubertModel  # type: ignore
            log.info("Loading Hubert (%s) via transformers...", HUBERT_MODEL_ID)
            self.hubert = HubertModel.from_pretrained(
                HUBERT_MODEL_ID,
                cache_dir=HF_CACHE,
            ).to("cuda").half().eval()
            log.info("Hubert loaded (transformers backend)")
        except Exception as e:  # noqa: BLE001
            log.exception("Failed to load Hubert: %s", e)
            self.hubert = None

        try:
            from rvc.rmvpe import RMVPE
            self.rmvpe = RMVPE(rmvpe_path, is_half=True, device="cuda")
            log.info("rmvpe loaded")
        except Exception as e:  # noqa: BLE001
            log.warning("RMVPE not loaded (will use crepe/pm fallback): %s", e)
            self.rmvpe = None

    def load(self, model_id: str) -> "RVCModel":
        """Charge un modèle RVC depuis le Volume (ou retourne du cache)."""
        if model_id in self._cache:
            model, _ = self._cache[model_id]
            self._cache[model_id] = (model, time.time())
            return model

        # LRU eviction
        if len(self._cache) >= self.cache_size:
            oldest_id = min(self._cache.items(), key=lambda kv: kv[1][1])[0]
            log.info("RVC cache eviction: %s", oldest_id)
            del self._cache[oldest_id]
            import torch
            torch.cuda.empty_cache()

        pth_path = Path(RVC_VOLUME_PATH) / model_id / "model.pth"
        index_path = Path(RVC_VOLUME_PATH) / model_id / "added.index"

        if not pth_path.exists():
            raise FileNotFoundError(f"RVC model not found: {pth_path}")

        log.info("Loading RVC model %s from %s ...", model_id, pth_path)
        model = RVCModel(
            pth_path=str(pth_path),
            index_path=str(index_path) if index_path.exists() else None,
            hubert=self.hubert,
            rmvpe=self.rmvpe,
        )
        self._cache[model_id] = (model, time.time())
        return model


class RVCModel:
    """Un modèle RVC chargé en VRAM."""

    def __init__(self, pth_path: str, index_path: str | None,
                 hubert, rmvpe):
        self.pth_path = pth_path
        self.index_path = index_path
        self.hubert = hubert
        self.rmvpe = rmvpe

        import torch
        ckpt = torch.load(pth_path, map_location="cpu", weights_only=False)

        self.tgt_sr = ckpt.get("config", [None] * 16)[15] or 40000
        self.f0 = ckpt.get("f0", True)
        self.version = ckpt.get("version", "v2")

        try:
            from rvc.synthesizer import SynthesizerTrnMs768NSFsid
            self.net_g = SynthesizerTrnMs768NSFsid(
                *ckpt["config"], is_half=True
            )
            self.net_g.load_state_dict(ckpt["weight"], strict=False)
            self.net_g = self.net_g.to("cuda").half().eval()
        except ImportError:
            log.error("rvc-python not installed - RVC unavailable")
            raise

        self.index = None
        self.big_npy = None
        if index_path:
            try:
                import faiss
                self.index = faiss.read_index(index_path)
                self.big_npy = self.index.reconstruct_n(0, self.index.ntotal)
                log.info("FAISS index loaded: %d vectors", self.index.ntotal)
            except Exception as e:
                log.warning("Failed to load FAISS index: %s", e)

    def convert(self, audio_b64: str, pitch_shift: int = 0,
                index_rate: float = 0.7) -> str:
        """Convertit un audio complet via RVC. Retourne base64 WAV."""
        audio_bytes = base64.b64decode(audio_b64)
        audio, sr = sf.read(io.BytesIO(audio_bytes))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32)

        if sr != 16000:
            audio_16k = self._resample(audio, sr, 16000)
        else:
            audio_16k = audio

        converted = self._infer(audio_16k, pitch_shift, index_rate)

        buf = io.BytesIO()
        sf.write(buf, converted, self.tgt_sr, format="WAV", subtype="PCM_16")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def convert_streaming(
        self, audio: np.ndarray
    ) -> Generator[tuple[str, int], None, None]:
        """Conversion streaming : prend un np.ndarray complet et yield des chunks.

        Note : RVC ne supporte pas vraiment le streaming temps réel (l'inférence
        est globale). On découpe l'output en chunks pour minimiser la latence
        perçue côté client.
        """
        if isinstance(audio, np.ndarray) and audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        converted = self._infer(audio, pitch_shift=0, index_rate=0.7)

        seq = 0
        for i in range(0, len(converted), STREAM_CHUNK_SAMPLES):
            chunk = converted[i:i + STREAM_CHUNK_SAMPLES]
            pcm = (chunk * 32767.0).astype(np.int16).tobytes()
            chunk_b64 = base64.b64encode(pcm).decode("ascii")
            yield chunk_b64, seq
            seq += 1

    def _infer(self, audio_16k: np.ndarray, pitch_shift: int,
               index_rate: float) -> np.ndarray:
        """Inférence RVC réelle.

        Squelette à compléter selon le package RVC final utilisé (rvc-python,
        applio, etc.). L'API exacte varie d'une version à l'autre.
        """
        import torch

        with torch.no_grad():
            # Hubert via transformers : input_values shape (batch, samples)
            # → output_hidden_states pour récupérer le layer 12 (RVC v2)
            audio_tensor = torch.from_numpy(audio_16k).float().to("cuda").unsqueeze(0)
            outputs = self.hubert(
                input_values=audio_tensor,
                output_hidden_states=True,
            )
            # hidden_states est un tuple (n_layers + 1) : index 0 = embeddings,
            # 1..12 = sortie de chaque layer transformer.
            # Pour RVC v2, on prend le layer 12 (le dernier).
            layer_idx = HUBERT_OUTPUT_LAYER if self.version == "v2" else 9
            feats = outputs.hidden_states[layer_idx]

        if self.f0:
            if self.rmvpe:
                f0 = self.rmvpe.infer_from_audio(audio_16k, thred=0.03)
            else:
                f0 = self._estimate_f0_simple(audio_16k)

            if pitch_shift != 0:
                f0 = f0 * (2 ** (pitch_shift / 12))
        else:
            f0 = None

        if self.index is not None and index_rate > 0:
            feats_np = feats.cpu().numpy()
            distances, indices = self.index.search(feats_np[0], 1)
            faiss_feats = self.big_npy[indices[0]]
            faiss_feats = torch.from_numpy(faiss_feats).to("cuda").unsqueeze(0)
            feats = faiss_feats * index_rate + feats * (1 - index_rate)

        with torch.no_grad():
            audio_out = self.net_g.infer(
                feats,
                f0=f0 if self.f0 else None,
                rate=1.0,
            )[0][0, 0].cpu().numpy()

        return audio_out

    @staticmethod
    def _resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
        ratio = sr_out / sr_in
        new_len = int(len(audio) * ratio)
        x_old = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, new_len, endpoint=False)
        return np.interp(x_new, x_old, audio).astype(np.float32)

    @staticmethod
    def _estimate_f0_simple(audio: np.ndarray) -> np.ndarray:
        """Fallback F0 si rmvpe absent. Utilise pyworld."""
        try:
            import pyworld
            f0, t = pyworld.dio(audio.astype(np.float64), 16000)
            f0 = pyworld.stonemask(audio.astype(np.float64), f0, t, 16000)
            return f0.astype(np.float32)
        except ImportError:
            log.warning("pyworld not available, F0 set to zeros")
            return np.zeros(len(audio) // 160, dtype=np.float32)
