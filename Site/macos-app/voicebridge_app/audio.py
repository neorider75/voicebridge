"""Capture micro + injection BlackHole via PyAudio.

Mode bypass (pause) : le micro réel est routé directement vers BlackHole
(latence ~10 ms). Mode normal : capture envoyée au VPS, audio cloné reçu
puis injecté dans BlackHole.
"""
from __future__ import annotations

import logging
import threading

try:
    import pyaudio  # type: ignore
    PYAUDIO_OK = True
except ImportError:
    pyaudio = None  # type: ignore
    PYAUDIO_OK = False

log = logging.getLogger("voicebridge.audio")

CAPTURE_RATE = 16000  # Spec V1 — Live envoie en 16 kHz mono
CAPTURE_CHANNELS = 1
CAPTURE_CHUNK = 1600  # 100 ms à 16 kHz


def list_input_devices() -> list[dict]:
    if not PYAUDIO_OK:
        return []
    pa = pyaudio.PyAudio()
    try:
        devs = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                devs.append({"index": i, "name": info["name"]})
        return devs
    finally:
        pa.terminate()


def list_output_devices() -> list[dict]:
    if not PYAUDIO_OK:
        return []
    pa = pyaudio.PyAudio()
    try:
        devs = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) > 0:
                devs.append({"index": i, "name": info["name"]})
        return devs
    finally:
        pa.terminate()


def find_blackhole_index() -> int | None:
    for d in list_output_devices():
        if "blackhole" in d["name"].lower():
            return d["index"]
    return None


class AudioPipeline:
    """Capture continu (toujours actif), envoi/réception conditionnel selon
    pause/online. Thread-safe.
    """

    def __init__(self, on_chunk_captured) -> None:
        self.on_chunk_captured = on_chunk_captured  # callback(bytes)
        self._pa = None
        self._in_stream = None
        self._out_stream = None
        self._thread = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.input_device = None
        self.output_device = None

    def start(self, input_idx: int | None, output_idx: int | None) -> None:
        if not PYAUDIO_OK:
            log.warning("pyaudio non installé — pipeline désactivé")
            return
        self.input_device = input_idx
        self.output_device = output_idx
        self._pa = pyaudio.PyAudio()
        self._in_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CAPTURE_CHANNELS,
            rate=CAPTURE_RATE,
            input=True,
            input_device_index=input_idx,
            frames_per_buffer=CAPTURE_CHUNK,
        )
        if output_idx is not None:
            self._out_stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=CAPTURE_CHANNELS,
                rate=24000,  # NeuTTS sortie
                output=True,
                output_device_index=output_idx,
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._in_stream.read(CAPTURE_CHUNK, exception_on_overflow=False)
            except Exception:  # noqa: BLE001
                continue
            if self._paused.is_set():
                # Bypass : pousser le micro réel directement dans BlackHole
                if self._out_stream is not None:
                    try:
                        self._out_stream.write(data)
                    except Exception:  # noqa: BLE001
                        pass
                continue
            try:
                self.on_chunk_captured(data)
            except Exception:  # noqa: BLE001
                log.exception("on_chunk_captured failed")

    def play_response(self, audio_bytes: bytes) -> None:
        """À appeler depuis le ws_client quand un audio_chunk arrive du serveur."""
        if self._out_stream is not None:
            try:
                self._out_stream.write(audio_bytes)
            except Exception:  # noqa: BLE001
                log.exception("output write failed")

    def pause(self, value: bool) -> None:
        if value:
            self._paused.set()
        else:
            self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._in_stream:
            try:
                self._in_stream.stop_stream(); self._in_stream.close()
            except Exception:  # noqa: BLE001
                pass
        if self._out_stream:
            try:
                self._out_stream.stop_stream(); self._out_stream.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pa:
            self._pa.terminate()
