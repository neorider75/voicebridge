"""Silero VAD wrapper.

Le package ``silero-vad`` (>= 4.0) expose ``load_silero_vad()`` et
``VADIterator``. Le modèle attend du **float32 mono à 16 kHz**, par chunks
de 512 échantillons (≈ 32 ms).

Cf. https://github.com/snakers4/silero-vad

Le wrapper est *import-safe* : aucun import lourd (torch, silero_vad) au
chargement du module.
"""
from __future__ import annotations

import logging

from . import manager as mgr

log = logging.getLogger("voicebridge.vad")

VAD_SAMPLE_RATE = 16000
VAD_CHUNK_SAMPLES = 512  # 32 ms à 16 kHz


def _load_silero():
    from silero_vad import load_silero_vad  # type: ignore
    return load_silero_vad()


def register_loaders() -> None:
    mgr.manager.register_loader(mgr.MODEL_SILERO_VAD, _load_silero)


def make_iterator(threshold: float = 0.5):
    """Crée un ``VADIterator`` neuf (à utiliser pour une session WebSocket)."""
    from silero_vad import VADIterator  # type: ignore
    model = mgr.manager.get(mgr.MODEL_SILERO_VAD)
    return VADIterator(model, threshold=threshold, sampling_rate=VAD_SAMPLE_RATE)
