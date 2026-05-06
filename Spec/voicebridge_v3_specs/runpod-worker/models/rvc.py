"""Wrapper RVC (Retrieval-based Voice Conversion) pour la conversion de voix.

Charge dynamiquement les modèles .pth utilisateur depuis le RunPod Network Volume.
Cache LRU des derniers modèles utilisés (max 3 en VRAM ~6 Go pour des .pth ~150 Mo).

Architecture :
- RVCRouter : gère le cache et le chargement des modèles
- RVCModel : un .pth chargé, expose convert() et convert_streaming()

Workflow :
1. Modèle entraîné sur Kaggle par l'utilisateur (gratuit)
2. .pth + .index uploadés depuis VoiceBridge sur RunPod Volume
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

# Streaming chunk en samples (24000 sample_rate * 0.2s = 4800 samples)
STREAM_CHUNK_SAMPLES = 4800


class RVCRouter:
    """Cache LRU des modèles RVC chargés en VRAM."""
    
    def __init__(self, cache_size: int = 3):
        self.cache_size = cache_size
        self._cache: dict[str, tuple["RVCModel", float]] = {}
        # Pré-charger hubert + rmvpe (modèles de base partagés)
        self._init_shared_models()
    
    def _init_shared_models(self):
        """Charge hubert_base + rmvpe une fois, partagés entre tous les RVCModel."""
        import torch
        hubert_path = os.path.join(RVC_ASSETS_PATH, "hubert_base.pt")
        rmvpe_path = os.path.join(RVC_ASSETS_PATH, "rmvpe.pt")
        
        if not os.path.exists(hubert_path):
            log.warning("hubert_base.pt missing at %s", hubert_path)
            self.hubert = None
            self.rmvpe = None
            return
        
        try:
            from fairseq import checkpoint_utils
            models, _, _ = checkpoint_utils.load_model_ensemble_and_task(
                [hubert_path], suffix=""
            )
            self.hubert = models[0].to("cuda").half().eval()
            log.info("hubert_base loaded")
        except Exception as e:
            log.exception("Failed to load hubert_base: %s", e)
            self.hubert = None
        
        try:
            # RMVPE est utilisé pour la détection de pitch (F0)
            from rvc.rmvpe import RMVPE  # depuis le package rvc-python
            self.rmvpe = RMVPE(rmvpe_path, is_half=True, device="cuda")
            log.info("rmvpe loaded")
        except Exception as e:
            log.warning("RMVPE not loaded (will use crepe/pm fallback): %s", e)
            self.rmvpe = None
    
    def load(self, model_id: str) -> "RVCModel":
        """Charge un modèle RVC depuis le Volume (ou retourne du cache)."""
        if model_id in self._cache:
            # Update last_used
            model, _ = self._cache[model_id]
            self._cache[model_id] = (model, time.time())
            return model
        
        # Eviction LRU
        if len(self._cache) >= self.cache_size:
            oldest_id = min(self._cache.items(), key=lambda kv: kv[1][1])[0]
            log.info("RVC cache eviction: %s", oldest_id)
            del self._cache[oldest_id]
            import torch
            torch.cuda.empty_cache()
        
        # Charger
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
        
        # Extraire les paramètres
        self.tgt_sr = ckpt.get("config", [None]*16)[15] or 40000
        self.f0 = ckpt.get("f0", True)
        self.version = ckpt.get("version", "v2")
        
        # Charger le générateur RVC
        # Note : l'API exacte dépend de la version du package RVC utilisé
        # (rvc-python, applio, etc.). Code à adapter selon le package final.
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
        
        # Charger l'index FAISS si dispo (améliore la qualité)
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
    
    def convert(
        self,
        audio_b64: str,
        pitch_shift: int = 0,
        index_rate: float = 0.7,
    ) -> str:
        """Convertit un audio complet via RVC.
        
        Args:
            audio_b64: WAV encodé en base64
            pitch_shift: décalage en semi-tons (0 = pas de shift)
            index_rate: poids de l'index FAISS [0.0, 1.0]
        
        Returns:
            base64 du WAV converti
        """
        # Décoder
        audio_bytes = base64.b64decode(audio_b64)
        audio, sr = sf.read(io.BytesIO(audio_bytes))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32)
        
        # Resample à 16kHz pour hubert
        if sr != 16000:
            audio_16k = self._resample(audio, sr, 16000)
        else:
            audio_16k = audio
        
        # Inférence RVC (squelette - dépend du package final)
        converted = self._infer(audio_16k, pitch_shift, index_rate)
        
        # Encoder en WAV PCM 16-bit
        buf = io.BytesIO()
        sf.write(buf, converted, self.tgt_sr, format="WAV", subtype="PCM_16")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    
    def convert_streaming(
        self,
        audio: np.ndarray,
    ) -> Generator[tuple[str, int], None, None]:
        """Conversion streaming : prend un np.ndarray complet et yield des chunks.
        
        Note : RVC ne supporte pas vraiment le streaming temps réel (l'inférence
        est globale). On découpe l'output en chunks pour minimiser la latence
        perçue côté client.
        """
        # Inférence complète
        if isinstance(audio, np.ndarray) and audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        
        converted = self._infer(audio, pitch_shift=0, index_rate=0.7)
        
        # Découper en chunks
        seq = 0
        for i in range(0, len(converted), STREAM_CHUNK_SAMPLES):
            chunk = converted[i:i + STREAM_CHUNK_SAMPLES]
            pcm = (chunk * 32767.0).astype(np.int16).tobytes()
            chunk_b64 = base64.b64encode(pcm).decode("ascii")
            yield chunk_b64, seq
            seq += 1
    
    def _infer(self, audio_16k: np.ndarray, pitch_shift: int, index_rate: float) -> np.ndarray:
        """Inférence RVC réelle.
        
        SQUELETTE - à compléter selon le package RVC final utilisé.
        """
        import torch
        
        # 1. Extraction features via hubert
        with torch.no_grad():
            audio_tensor = torch.from_numpy(audio_16k).float().to("cuda").unsqueeze(0)
            feats = self.hubert.extract_features(audio_tensor)[0]
        
        # 2. F0 estimation via rmvpe (ou alternative)
        if self.f0:
            if self.rmvpe:
                f0 = self.rmvpe.infer_from_audio(audio_16k, thred=0.03)
            else:
                # Fallback simpliste
                f0 = self._estimate_f0_simple(audio_16k)
            
            # Pitch shift
            if pitch_shift != 0:
                f0 = f0 * (2 ** (pitch_shift / 12))
        else:
            f0 = None
        
        # 3. Index FAISS lookup pour ressembler davantage à la voix cible
        if self.index is not None and index_rate > 0:
            feats_np = feats.cpu().numpy()
            distances, indices = self.index.search(feats_np[0], 1)
            faiss_feats = self.big_npy[indices[0]]
            faiss_feats = torch.from_numpy(faiss_feats).to("cuda").unsqueeze(0)
            feats = faiss_feats * index_rate + feats * (1 - index_rate)
        
        # 4. Génération via net_g
        with torch.no_grad():
            # API exacte dépend de la version RVC (à adapter)
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
