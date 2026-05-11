"""Capture micro + injection BlackHole via ``sounddevice``.

Note : on utilise ``sounddevice`` (et pas ``pyaudio``) parce que sa wheel pip
embarque PortAudio binaire — pas besoin de ``brew install portaudio`` côté
build.

Mode bypass (pause) : le micro réel est routé directement vers BlackHole
(latence ~10 ms). Mode normal : capture envoyée au VPS, audio cloné reçu
puis injecté dans BlackHole.
"""
from __future__ import annotations

import logging
import os
import threading
from queue import Empty, Queue

try:
    import numpy as np  # type: ignore
    import sounddevice as sd  # type: ignore
    SD_OK = True
except ImportError:
    np = None  # type: ignore
    sd = None  # type: ignore
    SD_OK = False

log = logging.getLogger("voicebridge.audio")

CAPTURE_RATE = 16000  # 16 kHz mono — Live spec
CAPTURE_CHANNELS = 1
CAPTURE_DTYPE = "int16"
CAPTURE_BLOCKSIZE = 1600  # 100 ms à 16 kHz

OUTPUT_RATE = 24000  # NeuTTS → 24 kHz
OUTPUT_CHANNELS = 1
OUTPUT_DTYPE = "int16"
# Buffer de sortie tuned pour la latence (BlackHole → Teams/Zoom).
# 480 samples @ 24 kHz = 20 ms. Combiné avec le buffer PortAudio interne
# (~50 ms en moyenne sur macOS) ça donne une latence end-to-end ~70 ms
# côté sortie, vs ~150-300 ms avec blocksize=0 (default ~5120 samples).
# Override via env VB_OUTPUT_BLOCKSIZE.
OUTPUT_BLOCKSIZE = int(os.environ.get("VB_OUTPUT_BLOCKSIZE", "480"))


def list_input_devices() -> list[dict]:
    if not SD_OK:
        return []
    return [
        {"index": i, "name": d["name"]}
        for i, d in enumerate(sd.query_devices())
        if d.get("max_input_channels", 0) > 0
    ]


def list_output_devices() -> list[dict]:
    if not SD_OK:
        return []
    return [
        {"index": i, "name": d["name"]}
        for i, d in enumerate(sd.query_devices())
        if d.get("max_output_channels", 0) > 0
    ]


def find_blackhole_index() -> int | None:
    for d in list_output_devices():
        if "blackhole" in d["name"].lower():
            return d["index"]
    return None


class AudioPipeline:
    """Pipeline duplex : capture en continu + sortie vers BlackHole.

    Le callback ``on_chunk_captured(bytes)`` reçoit le PCM 16-bit 16 kHz mono
    en chunks de ~100 ms. Le mode pause passe en bypass (micro réel → BlackHole
    directement, sans aller-retour serveur).
    """

    def __init__(self, on_chunk_captured, on_level=None) -> None:
        """``on_level(speaking: bool)`` — appelé quand l'état parole/silence
        change (transitions seulement, pas à chaque chunk). Permet à l'UI
        de basculer l'icône en menu bar entre 🟢 (idle) et 🎤 (en parole)."""
        self.on_chunk_captured = on_chunk_captured
        self.on_level = on_level or (lambda speaking: None)
        self._in_stream = None
        self._out_stream = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._out_queue: Queue[bytes] = Queue()
        self._writer_thread: threading.Thread | None = None
        self.input_device = None
        self.output_device = None
        # ── Détection parole/silence locale (pour le retour visuel) ──
        # Seuils RMS (0..1) avec hystérésis pour éviter le flicker.
        self._speaking = False
        self._speech_counter = 0   # ticks consécutifs au-dessus du seuil
        self._silence_counter = 0  # ticks consécutifs en-dessous
        # ── Noise gate : ring buffer pre-roll de 3 chunks (~300 ms) ──
        # Quand on transite silence→parole, on flushe ces N chunks en plus
        # du chunk courant pour ne pas couper le début du mot.
        self._preroll_buffer: list[bytes] = []
        self._preroll_max = 3

    # ── Public ───────────────────────────────────────────────────────

    def start(self, input_idx: int | None, output_idx: int | None) -> None:
        if not SD_OK:
            log.warning("sounddevice non installé — pipeline désactivé")
            return
        self.input_device = input_idx
        self.output_device = output_idx

        # Sortie : RawOutputStream (PCM 16-bit) — on écrit via stream.write()
        # depuis un thread dédié pour éviter de bloquer le callback d'entrée.
        # blocksize=OUTPUT_BLOCKSIZE (480 samples / 20 ms par défaut) au lieu
        # de 0 (default ~5120/213 ms) → -150 ms de latence end-to-end côté
        # sortie BlackHole.
        self._out_stream = sd.RawOutputStream(
            samplerate=OUTPUT_RATE, channels=OUTPUT_CHANNELS, dtype=OUTPUT_DTYPE,
            device=output_idx, blocksize=OUTPUT_BLOCKSIZE,
            latency='low',  # demande à PortAudio le buffer le plus petit possible
        )
        self._out_stream.start()
        log.info("AudioPipeline output stream: rate=%d blocksize=%d "
                 "latency=%.3fs", OUTPUT_RATE, OUTPUT_BLOCKSIZE,
                 self._out_stream.latency)
        self._stop.clear()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

        self._callback_count = 0

        def _in_callback(indata, frames, time_info, status):  # noqa: ARG001
            # ``indata`` est un cffi buffer (CData) en mode raw → bytes()
            data_bytes = bytes(indata)

            # Log 1-shot pour confirmer que le callback s'exécute bien
            self._callback_count += 1
            if self._callback_count == 1:
                log.info("AudioPipeline _in_callback FIRST tick: "
                         "%d bytes, status=%s", len(data_bytes), status)
            elif self._callback_count == 50:
                log.info("AudioPipeline _in_callback: 50 ticks OK (~5s)")

            # ── Détection parole/silence locale (icône menu bar) ──
            try:
                self._update_speaking_state(data_bytes)
            except Exception:  # noqa: BLE001
                log.exception("_update_speaking_state failed")

            if self._paused.is_set():
                # Bypass : envoie le micro réel direct dans BlackHole.
                # Resample 16k → 24k brutalement (zero-stuff x1.5) — peu propre
                # mais latence minimale (à raffiner en V1.1).
                self._out_queue.put(self._upsample_16k_to_24k(data_bytes))
            else:
                # ── Noise gate : on n'envoie au serveur QUE quand la voix
                # est détectée (évite que le VAD Silero serveur déclenche
                # sur les bruits ambiants / voix en arrière-plan).
                # Désactivable via VB_NOISE_GATE=0 pour debug.
                gate_enabled = os.environ.get("VB_NOISE_GATE", "1") == "1"
                if gate_enabled and not self._speaking:
                    # En silence : on alimente quand même le ring buffer
                    # pour pouvoir flusher les ~300 ms qui précèdent la
                    # prochaine détection de parole (sinon début de mot
                    # coupé).
                    self._preroll_buffer.append(data_bytes)
                    if len(self._preroll_buffer) > self._preroll_max:
                        self._preroll_buffer.pop(0)
                    return  # ← drop : on n'envoie rien au serveur

                # Flush du pre-roll au moment précis du passage en speaking
                # (cf. _update_speaking_state qui a flippé _speaking=True
                # juste avant ce return-early manqué).
                if self._preroll_buffer:
                    for chunk in self._preroll_buffer:
                        try:
                            self.on_chunk_captured(chunk)
                        except Exception:  # noqa: BLE001
                            log.exception("on_chunk_captured (preroll) failed")
                    self._preroll_buffer.clear()

                try:
                    self.on_chunk_captured(data_bytes)
                except Exception:  # noqa: BLE001
                    log.exception("on_chunk_captured failed")

        log.info("AudioPipeline opening input stream: device=%s rate=%d "
                 "channels=%d dtype=%s blocksize=%d",
                 input_idx, CAPTURE_RATE, CAPTURE_CHANNELS,
                 CAPTURE_DTYPE, CAPTURE_BLOCKSIZE)
        try:
            self._in_stream = sd.RawInputStream(
                samplerate=CAPTURE_RATE, channels=CAPTURE_CHANNELS,
                dtype=CAPTURE_DTYPE, device=input_idx,
                blocksize=CAPTURE_BLOCKSIZE, callback=_in_callback,
            )
            self._in_stream.start()
            log.info("AudioPipeline input stream STARTED (active=%s)",
                     self._in_stream.active)
        except Exception as exc:  # noqa: BLE001
            log.exception("AudioPipeline input stream FAILED to open: %s", exc)
            raise

    def play_response(self, audio_bytes: bytes) -> None:
        """Le ws_client appelle ça quand un audio_chunk arrive du serveur."""
        if audio_bytes:
            self._out_queue.put(audio_bytes)

    def pause(self, value: bool) -> None:
        if value:
            self._paused.set()
        else:
            self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def stop(self) -> None:
        self._stop.set()
        for s in (self._in_stream, self._out_stream):
            if s is not None:
                try:
                    s.stop(); s.close()
                except Exception:  # noqa: BLE001
                    pass
        if self._writer_thread:
            self._writer_thread.join(timeout=2)

    # ── Internal ────────────────────────────────────────────────────

    def _writer_loop(self) -> None:
        # Frame de silence (20 ms à 24 kHz mono int16 = OUTPUT_BLOCKSIZE * 2 bytes)
        # injecté quand la queue est vide. Sans ça, BlackHole/macOS CoreAudio
        # suspend le pipeline en idle et le chunk suivant reste bloqué jusqu'à
        # ce qu'un événement (parole, autre audio) le "réveille" — d'où le
        # symptôme "le son de la phrase précédente sort quand j'entame la
        # suivante". Le silence garde le device actif en permanence.
        silence_frame = b"\x00" * (OUTPUT_BLOCKSIZE * 2)
        # Timeout court (10 ms) pour que le silence s'injecte rapidement
        # quand il n'y a pas de chunk en attente, sans CPU spinning.
        while not self._stop.is_set():
            try:
                chunk = self._out_queue.get(timeout=0.01)
            except Empty:
                if self._out_stream is not None:
                    try:
                        self._out_stream.write(silence_frame)
                    except Exception:  # noqa: BLE001
                        pass
                continue
            if self._out_stream is None:
                continue
            try:
                self._out_stream.write(chunk)
            except Exception:  # noqa: BLE001
                log.exception("output write failed")

    def _update_speaking_state(self, pcm_int16_bytes: bytes) -> None:
        """Détecte parole/silence sur le chunk capturé via RMS + hystérésis.

        Appelle ``self.on_level(speaking)`` UNIQUEMENT lors des transitions
        silence→parole et parole→silence (pas à chaque chunk, pour ne pas
        spammer le main thread Cocoa).

        Seuils permissifs (volontairement bas) — un micro Mac intégré
        en open-space capte facilement du bruit ambiant à RMS ~0.003-0.005,
        et la voix parlée monte à 0.01-0.05 selon la distance.
        - RMS > 0.008 : probable parole
        - RMS < 0.003 : silence
        Hystérésis 1 / 6 ticks (~32 ms / 192 ms à 16 kHz / 512 samples).

        Overridable via env var VB_RMS_SPEAK_THRESHOLD (override seuil parole)
        et VB_RMS_SILENCE_THRESHOLD (override seuil silence) pour debug.
        Log toutes les 50 chunks (~1.6 s) le RMS courant si VB_DEBUG_RMS=1.
        """
        if np is None:
            return
        x = np.frombuffer(pcm_int16_bytes, dtype=np.int16)
        if x.size == 0:
            return
        # RMS normalisé en 0..1 (int16 max = 32768)
        rms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) / 32768.0

        speak_th = float(os.environ.get("VB_RMS_SPEAK_THRESHOLD", "0.008"))
        silence_th = float(os.environ.get("VB_RMS_SILENCE_THRESHOLD", "0.003"))

        if os.environ.get("VB_DEBUG_RMS") == "1":
            self._rms_log_counter = getattr(self, "_rms_log_counter", 0) + 1
            if self._rms_log_counter % 50 == 0:
                log.info("mic RMS=%.5f speaking=%s (thresholds: %.4f / %.4f)",
                         rms, self._speaking, speak_th, silence_th)

        if rms > speak_th:
            self._speech_counter += 1
            self._silence_counter = 0
            if not self._speaking and self._speech_counter >= 1:
                self._speaking = True
                log.info("mic → SPEAKING (rms=%.5f > %.4f)", rms, speak_th)
                self.on_level(True)
        elif rms < silence_th:
            self._silence_counter += 1
            self._speech_counter = 0
            if self._speaking and self._silence_counter >= 6:
                self._speaking = False
                log.info("mic → SILENCE (rms=%.5f < %.4f)", rms, silence_th)
                self.on_level(False)

    @staticmethod
    def _upsample_16k_to_24k(pcm_int16_bytes: bytes) -> bytes:
        """Upsample brut 16 kHz → 24 kHz par interpolation linéaire (mono).

        Utilisé en mode bypass uniquement (latence prioritaire sur qualité).
        """
        if np is None:
            return pcm_int16_bytes
        x = np.frombuffer(pcm_int16_bytes, dtype=np.int16)
        if len(x) == 0:
            return b""
        n_new = int(len(x) * 24000 / 16000)
        idx_old = np.linspace(0, 1, len(x), endpoint=False)
        idx_new = np.linspace(0, 1, n_new, endpoint=False)
        y = np.interp(idx_new, idx_old, x.astype(np.float32))
        return y.astype(np.int16).tobytes()
