"""Gestion centralisée des modèles ML — lazy loading + auto-unload.

Tous les modèles ML (NeuTTS Q4/Q8 FR+EN, Kyutai, Silero, détection deepfake)
passent par ce manager. Il :

- charge un modèle à la demande (~3-5 s la première fois)
- enregistre le timestamp du dernier usage
- décharge les modèles inactifs après ``model_unload_after_minutes`` min
  (configurable via ``config.json``)

Le manager est thread-safe (verrou par clé de modèle).

Conception "import-safe" : les imports lourds (``torch``, ``transformers``,
``neutts``) ne sont pas faits à l'import du module, mais à l'intérieur des
factory functions, pour que ``app/main.py`` reste importable même quand les
dépendances ML ne sont pas installées (cas du dev local Mac).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import config

log = logging.getLogger("voicebridge.models")

# Identifiants stables exposés à l'API/UI.
MODEL_NEUTTS_FR_Q4 = "neutts_fr_q4"
MODEL_NEUTTS_EN_Q4 = "neutts_en_q4"
MODEL_NEUTTS_FR_Q8 = "neutts_fr_q8"
MODEL_NEUTTS_EN_Q8 = "neutts_en_q8"
MODEL_XTTS_V2 = "xtts_v2"  # Coqui XTTS-v2 (multilingue, ~1.7B params)
MODEL_KYUTAI = "kyutai"
MODEL_DEEPFAKE_V2 = "deepfake_detection_v2"
MODEL_SILERO_VAD = "silero_vad"

ALL_MODEL_KEYS = (
    MODEL_NEUTTS_FR_Q4,
    MODEL_NEUTTS_EN_Q4,
    MODEL_NEUTTS_FR_Q8,
    MODEL_NEUTTS_EN_Q8,
    MODEL_XTTS_V2,
    MODEL_KYUTAI,
    MODEL_DEEPFAKE_V2,
    MODEL_SILERO_VAD,
)


@dataclass
class _ModelSlot:
    instance: Any | None = None
    last_used: float = 0.0
    lock: threading.RLock = field(default_factory=threading.RLock)


class ModelManager:
    """Singleton (au sens "une instance par process uvicorn workers=1")."""

    def __init__(self) -> None:
        self._slots: dict[str, _ModelSlot] = {k: _ModelSlot() for k in ALL_MODEL_KEYS}
        self._loaders: dict[str, Callable[[], Any]] = {}

    # ------------------------------------------------------------------
    # Enregistrement des factory functions
    # ------------------------------------------------------------------

    def register_loader(self, key: str, loader: Callable[[], Any]) -> None:
        if key not in self._slots:
            raise KeyError(f"Clé de modèle inconnue : {key}")
        self._loaders[key] = loader

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any:
        if key not in self._slots:
            raise KeyError(f"Clé de modèle inconnue : {key}")
        if key not in self._loaders:
            raise RuntimeError(f"Aucun loader enregistré pour {key}")
        slot = self._slots[key]
        with slot.lock:
            if slot.instance is None:
                t0 = time.time()
                log.info("loading model key=%s", key)
                slot.instance = self._loaders[key]()
                log.info("loaded model key=%s elapsed=%.2fs", key, time.time() - t0)
            slot.last_used = time.time()
            return slot.instance

    def is_loaded(self, key: str) -> bool:
        return self._slots[key].instance is not None

    def status_snapshot(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, slot in self._slots.items():
            out[key] = "loaded" if slot.instance is not None else "unloaded"
        return out

    def unload(self, key: str) -> bool:
        slot = self._slots[key]
        with slot.lock:
            if slot.instance is None:
                return False
            slot.instance = None
            log.info("unloaded model key=%s", key)
            return True

    def unload_all(self) -> int:
        n = 0
        for k in ALL_MODEL_KEYS:
            if self.unload(k):
                n += 1
        # Hint GC (pour libérer la VRAM/RAM côté torch)
        try:
            import gc
            gc.collect()
            import torch  # type: ignore
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
        return n

    def sweep_idle(self) -> int:
        """À appeler périodiquement (APScheduler) : décharge les modèles inactifs."""
        max_idle = max(60, int(config.get("model_unload_after_minutes", 15)) * 60)
        now = time.time()
        n = 0
        for key, slot in self._slots.items():
            with slot.lock:
                if slot.instance is not None and (now - slot.last_used) > max_idle:
                    slot.instance = None
                    log.info("auto-unloaded model key=%s idle=%ds", key, int(now - slot.last_used))
                    n += 1
        return n


# Instance unique exportée
manager = ModelManager()
