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
import time
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
        self._mic_muted = threading.Event()
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
        # ── Tail post-parole : on continue d'envoyer N chunks au serveur
        # APRÈS la transition parole→silence, pour laisser Silero VAD côté
        # serveur conclure son utterance et flusher le STT. Sans ça, le
        # serveur reste bloqué dans `in_speech=True` (silence_count gelé)
        # jusqu'à la prochaine phrase, qui débloque l'ancienne — bug
        # "la phrase précédente ne sort que quand je reparle".
        # 8 chunks × 100 ms = 800 ms — calibré juste au-dessus de
        # SILENCE_FLUSH_TICKS_GPU (~500 ms) avec marge pour jitter réseau.
        # Combiné aux 600 ms d'hystérésis silence client, le serveur reçoit
        # ~1.4 s de silence après parole, largement de quoi flusher sans
        # gaspiller de bande passante.
        self._tail_remaining = 0
        self._tail_max = int(os.environ.get("VB_GATE_TAIL_CHUNKS", "8"))
        # ── Half-duplex : mute le micro pendant la lecture de la voix synth ──
        # Sinon le mic capte la voix synthétisée (via haut-parleurs ou
        # loopback BlackHole) → Whisper la retranscrit → boucle infinie de
        # phrases dupliquées + hallucinations type "Merci". On garde un
        # timestamp jusqu'à quand bloquer l'envoi mic. Marge 300 ms après
        # la fin estimée de l'audio pour absorber la réverbération et le
        # ringing audio. Désactivable via VB_HALF_DUPLEX=0.
        self._mute_mic_until = 0.0
        self._half_duplex_margin_s = float(
            os.environ.get("VB_HALF_DUPLEX_MARGIN", "0.3"))

    # ── Public ───────────────────────────────────────────────────────

    def start(self, input_idx: int | None, output_idx: int | None) -> None:
        if not SD_OK:
            log.warning("sounddevice non installé — pipeline désactivé")
            return
        self.input_device = input_idx
        self.output_device = output_idx

        # Sortie : RawOutputStream en mode CALLBACK (PortAudio pull-driven).
        # PortAudio appelle _out_callback à son rythme naturel (= ce que
        # BlackHole consomme). Quand on a un chunk à jouer, on le fournit ;
        # sinon, du silence. Avantage critique : pas d'accumulation possible
        # (pas de write() bloquant). Plus de bug "phrase reste bloquée tant
        # que tu ne reparles pas".
        self._out_carry = bytearray()  # buffer interne pour reste de chunk
        self._out_cb_count = 0
        self._out_cb_with_data = 0
        # Diagnostic CoreAudio : suivre l'évolution du DAC time entre les
        # callbacks pour détecter si BlackHole stalle (output time qui
        # n'avance plus = device suspendu par macOS Energy Saver).
        self._last_cb_time = None
        self._last_dac_time = None
        self._dac_stall_count = 0
        # Logue le 1er callback "after silence" pour repérer les "wakes"
        # (transitions silence prolongé → vraies données).
        self._was_idle = True

        # Silence pur en idle (zéros). On a tenté du dither de bruit blanc
        # pour empêcher la suspension BlackHole/Teams VAD, mais ça créait
        # un bruit de fond audible désagréable. Override possible via
        # VB_DITHER_AMPLITUDE=N pour ré-activer si besoin (et debug).
        _dither_amp = int(os.environ.get("VB_DITHER_AMPLITUDE", "0"))
        if _dither_amp > 0 and np is not None:
            _dither_samples = np.random.randint(
                -_dither_amp, _dither_amp + 1,
                OUTPUT_BLOCKSIZE * 4, dtype=np.int16,
            )
            self._dither_buffer = bytes(_dither_samples.tobytes())
            log.info("AudioPipeline dither active: amplitude=%d", _dither_amp)
        else:
            self._dither_buffer = bytes(OUTPUT_BLOCKSIZE * 4 * 2)  # zéros
        self._dither_offset = 0

        def _dither_chunk(nbytes: int) -> bytes:
            """Retourne nbytes de dither, en bouclant sur _dither_buffer."""
            buf = self._dither_buffer
            blen = len(buf)
            off = self._dither_offset
            if off + nbytes <= blen:
                out = buf[off:off + nbytes]
                self._dither_offset = (off + nbytes) % blen
                return out
            # Wrap around
            part1 = buf[off:]
            remaining = nbytes - len(part1)
            part2 = buf[:remaining]
            self._dither_offset = remaining % blen
            return bytes(part1) + bytes(part2)

        def _out_callback(outdata, frames, time_info, status):  # noqa: ARG001
            need_bytes = frames * 2  # int16 mono
            out_view = memoryview(outdata)
            written = 0
            # 1) Vide d'abord le carry restant de la dernière itération
            if self._out_carry:
                take = min(len(self._out_carry), need_bytes - written)
                out_view[written:written + take] = self._out_carry[:take]
                del self._out_carry[:take]
                written += take
            # 2) Tire de la queue tant qu'il manque des bytes
            had_data = bool(self._out_carry) or written > 0
            while written < need_bytes:
                try:
                    chunk = self._out_queue.get_nowait()
                except Empty:
                    break
                had_data = True
                need = need_bytes - written
                if len(chunk) <= need:
                    out_view[written:written + len(chunk)] = chunk
                    written += len(chunk)
                else:
                    out_view[written:written + need] = chunk[:need]
                    self._out_carry.extend(chunk[need:])
                    written = need_bytes
                    break
            # 3) Complète avec du DITHER (pas de silence pur) si la queue
            #    est vide. Ça garde le VAD/DTX du consumer (Teams, Zoom)
            #    actif pendant les pauses entre phrases.
            if written < need_bytes:
                gap = need_bytes - written
                out_view[written:need_bytes] = _dither_chunk(gap)

            # Diagnostic CoreAudio : track DAC time progression
            self._out_cb_count += 1
            if had_data:
                self._out_cb_with_data += 1
            import time as _t
            wall_time = _t.monotonic()
            dac_time = getattr(time_info, "outputBufferDacTime", 0)

            # Détecte un stall DAC : si wall_time avance mais dac_time non
            if self._last_cb_time is not None and self._last_dac_time is not None:
                wall_delta = wall_time - self._last_cb_time
                dac_delta = dac_time - self._last_dac_time
                # Normal : dac_delta ≈ frames/sample_rate ≈ 0.02s à chaque cb
                # Stall : dac_delta = 0 mais wall_delta > 0 (souvent)
                if wall_delta > 0.030 and dac_delta < 0.001:
                    self._dac_stall_count += 1
                    if self._dac_stall_count in (1, 5, 20, 100):
                        log.warning("DAC STALL #%d: wall+%.3fs but dac+%.6fs "
                                    "(BlackHole frozen?)",
                                    self._dac_stall_count,
                                    wall_delta, dac_delta)
            self._last_cb_time = wall_time
            self._last_dac_time = dac_time

            # Log transition idle → data ("wake up event")
            if had_data and self._was_idle:
                log.info("WAKE UP from idle: cb #%d, queue had data after "
                         "silence (qsize=%d)",
                         self._out_cb_count, self._out_queue.qsize())
                self._was_idle = False
            elif not had_data:
                self._was_idle = True

            if self._out_cb_count in (1, 50, 200, 500, 1500):
                log.info("_out_callback tick #%d (with_data=%d, queue=%d, "
                         "stalls=%d, status=%s)",
                         self._out_cb_count, self._out_cb_with_data,
                         self._out_queue.qsize(), self._dac_stall_count, status)

        self._out_stream = sd.RawOutputStream(
            samplerate=OUTPUT_RATE, channels=OUTPUT_CHANNELS, dtype=OUTPUT_DTYPE,
            device=output_idx, blocksize=OUTPUT_BLOCKSIZE,
            latency='low',
            callback=_out_callback,
        )
        self._out_stream.start()
        log.info("AudioPipeline output stream (callback mode): "
                 "rate=%d blocksize=%d latency=%.3fs",
                 OUTPUT_RATE, OUTPUT_BLOCKSIZE, self._out_stream.latency)
        self._stop.clear()
        # Plus besoin du writer thread — PortAudio pull via callback.

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
                # Mute mic : aucun chunk n'est envoyé au serveur. Pre-roll
                # vidé pour ne pas pousser des bouts d'audio antérieurs au
                # unmute.
                if self._mic_muted.is_set():
                    self._preroll_buffer.clear()
                    return

                # Half-duplex (opt-in via VB_HALF_DUPLEX=1) : on droppe le
                # chunk mic tant que la voix synthétisée est en cours de
                # lecture. Utile en test avec haut-parleurs (le mic capte
                # la synth → boucle). En prod (sortie BlackHole virtuelle,
                # casque séparé) c'est inutile et dégrade l'expérience
                # (impossible de couper la parole) → laissé OFF par défaut.
                if (os.environ.get("VB_HALF_DUPLEX", "0") == "1"
                        and time.monotonic() < self._mute_mic_until):
                    self._preroll_buffer.clear()
                    return

                gate_enabled = os.environ.get("VB_NOISE_GATE", "1") == "1"
                if gate_enabled and not self._speaking:
                    # En silence : on continue d'envoyer ``tail_max`` chunks
                    # APRÈS la fin de la parole pour laisser Silero VAD côté
                    # serveur conclure et flusher. Au-delà → drop pur.
                    if self._tail_remaining > 0:
                        self._tail_remaining -= 1
                        try:
                            self.on_chunk_captured(data_bytes)
                        except Exception:  # noqa: BLE001
                            log.exception("on_chunk_captured (tail) failed")
                        # Pas de return ici : on continue le ring buffer.
                    # Ring buffer pre-roll pour ne pas couper le début
                    # de la prochaine phrase.
                    self._preroll_buffer.append(data_bytes)
                    if len(self._preroll_buffer) > self._preroll_max:
                        self._preroll_buffer.pop(0)
                    return  # ← drop : on n'envoie rien au serveur (sauf tail)

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
            # Half-duplex : étend la fenêtre de mute mic jusqu'à la fin
            # estimée de la lecture de ce chunk + marge.
            # Durée chunk = bytes / 2 (int16) / OUTPUT_RATE
            chunk_duration_s = (len(audio_bytes) / 2) / OUTPUT_RATE
            # OFF par défaut : en prod, BlackHole = sortie virtuelle, pas
            # de loopback acoustique. Activer uniquement quand on monitore
            # via haut-parleurs (test/debug) → VB_HALF_DUPLEX=1.
            if os.environ.get("VB_HALF_DUPLEX", "0") == "1":
                now = time.monotonic()
                # On part de max(now, fin_actuelle) pour accumuler les chunks
                # successifs sans saut en arrière dans le temps.
                base = max(now, self._mute_mic_until)
                self._mute_mic_until = (
                    base + chunk_duration_s + self._half_duplex_margin_s)
            # Diagnostic : VB_RESPONSE_BEEP=1 préfixe un bip 880 Hz 100 ms
            # au premier chunk de chaque réponse (queue vide). Permet de
            # confirmer auditivement à quel moment exact l'audio synth sort.
            if (os.environ.get("VB_RESPONSE_BEEP") == "1"
                    and self._out_queue.empty() and np is not None):
                if not hasattr(self, "_beep_bytes"):
                    t = np.arange(int(OUTPUT_RATE * 0.1)) / OUTPUT_RATE
                    self._beep_bytes = (
                        np.sin(2 * np.pi * 880 * t) * 16000
                    ).astype(np.int16).tobytes()
                self._out_queue.put(self._beep_bytes)
                log.info("play_response: marker BEEP injected (start of utterance)")
            self._out_queue.put(audio_bytes)
            # Diagnostic : log la croissance de la queue
            qsize = self._out_queue.qsize()
            if qsize <= 2 or qsize % 10 == 0:
                log.info("play_response: queued %d bytes (qsize=%d)",
                         len(audio_bytes), qsize)

    def pause(self, value: bool) -> None:
        if value:
            self._paused.set()
        else:
            self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def set_mic_muted(self, value: bool) -> None:
        """Mute total du micro : aucun chunk audio n'est envoyé au serveur
        tant que le flag est levé. Différent de pause() qui passe en mode
        bypass (micro → BlackHole direct sans aller-retour)."""
        if value:
            self._mic_muted.set()
            log.info("mic MUTED")
        else:
            self._mic_muted.clear()
            log.info("mic UNMUTED")

    def is_mic_muted(self) -> bool:
        return self._mic_muted.is_set()

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
        # En mode callback (depuis le commit callback-based), _writer_thread
        # est None — pas de thread à join.

    # ── Internal ────────────────────────────────────────────────────

    # _writer_loop supprimé : on est désormais en mode callback PortAudio
    # (cf. _out_callback dans start()). PortAudio pull notre audio depuis
    # _out_queue à son rythme naturel, plus de thread writer dédié.

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
                # Active le tail post-parole : on continue d'envoyer
                # ``tail_max`` chunks au serveur pour laisser le VAD
                # conclure et flusher l'utterance (cf. note dans __init__).
                self._tail_remaining = self._tail_max
                log.info("mic → SILENCE (rms=%.5f < %.4f), tail=%d chunks",
                         rms, silence_th, self._tail_remaining)
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
