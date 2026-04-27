"""Wrapper NeuTTS Nano (Q4/Q8 × FR/EN).

API officielle (cf. github.com/neuphonic/neutts-air) :

    from neutts import NeuTTS
    tts = NeuTTS(backbone_repo=..., backbone_device='cpu',
                 codec_repo=..., codec_device='cpu')
    ref_codes = tts.encode_reference(wav_path)
    wav = tts.infer(text, ref_codes, ref_text)

Selon la version pip, le module peut s'appeler ``neutts`` ou ``neuttsair`` —
on essaie les deux à l'import.

Ce module ne charge **rien** au moment de son import : les modèles sont
créés via les factories enregistrées sur ``ModelManager``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .. import config
from . import manager as mgr

log = logging.getLogger("voicebridge.tts")


def _NeuTTSClass() -> type:
    """Import paresseux du wrapper NeuTTS (selon le nom de package installé)."""
    try:
        from neutts import NeuTTS  # type: ignore
        return NeuTTS
    except ImportError:
        try:
            from neuttsair.neutts import NeuTTSAir  # type: ignore
            return NeuTTSAir
        except ImportError as exc:
            raise RuntimeError(
                "Aucun package NeuTTS installé (essayé : neutts, neuttsair)."
            ) from exc


# ---------------------------------------------------------------------------
# Mapping : clé manager → (repo HuggingFace ID, qualité)
# ---------------------------------------------------------------------------
#
# On passe les repo IDs HuggingFace (et non des chemins filesystem) au
# constructeur NeuTTS. NeuTTS reconnaît "neuphonic/..." comme officiel,
# infère la langue/le format GGUF et résout le téléchargement depuis le
# cache HF local (variables HF_HOME / HUGGINGFACE_HUB_CACHE positionnées
# par voicebridge.service). Pas de re-download → résolution immédiate.

_BACKBONE_REPOS = {
    # FR : nano-french (0.2B) — seule famille publiée par Neuphonic en FR
    mgr.MODEL_NEUTTS_FR_Q4: ("neuphonic/neutts-nano-french-q4-gguf", "fr", "normal"),
    mgr.MODEL_NEUTTS_FR_Q8: ("neuphonic/neutts-nano-french-q8-gguf", "fr", "high"),
    # EN : nano (0.2B) — c'est ce qui a été utilisé pour la démo officielle
    # (NeuTTS-Nano-V4.mp4 sur le repo). Air (0.7B) existe et supporte aussi
    # le cloning, mais on reste sur nano-en pour matcher la baseline
    # démontrée publiquement par Neuphonic. Pour switcher sur Air :
    #   neuphonic/neutts-air-q4-gguf / neutts-air-q8-gguf
    mgr.MODEL_NEUTTS_EN_Q4: ("neuphonic/neutts-nano-q4-gguf", "en", "normal"),
    mgr.MODEL_NEUTTS_EN_Q8: ("neuphonic/neutts-nano-q8-gguf", "en", "high"),
}

CODEC_REPO = "neuphonic/neucodec"


def model_key_for(language: str, quality: str) -> str:
    quality = "high" if quality == "high" else "normal"
    if language == "fr":
        return mgr.MODEL_NEUTTS_FR_Q8 if quality == "high" else mgr.MODEL_NEUTTS_FR_Q4
    if language == "en":
        return mgr.MODEL_NEUTTS_EN_Q8 if quality == "high" else mgr.MODEL_NEUTTS_EN_Q4
    raise ValueError(f"langue non supportée : {language}")


def _patch_infer_ggml_temperature(instance, temperature: float, top_k: int) -> bool:
    """Remplace _infer_ggml de l'instance NeuTTS pour utiliser une
    temperature/top_k configurables (au lieu du 1.0/50 hardcodés). Plus de
    temperature = plus d'expressivité prosodique (intonation moins plate).
    Trade-off : risque d'artefacts si trop haut. 1.1-1.2 est safe.

    Retourne True si patché avec succès.
    """
    if not hasattr(instance, "_infer_ggml"):
        return False
    try:
        import re  # noqa: F401  (utilisé indirectement par le re-décodage)

        def patched(self, ref_codes, ref_text, input_text):
            # Recopie de la logique de _infer_ggml mais avec temperature/top_k
            # pris depuis self.temperature / self.top_k.
            ref_text_p = self._to_phones(ref_text)
            input_text_p = self._to_phones(input_text)
            codes_str = "".join([f"<|speech_{idx}|>" for idx in ref_codes])
            prompt = (
                f"user: Convert the text to speech:<|TEXT_PROMPT_START|>"
                f"{ref_text_p} {input_text_p}"
                f"<|TEXT_PROMPT_END|>\nassistant:"
                f"<|SPEECH_GENERATION_START|>{codes_str}"
            )
            output = self.backbone(
                prompt,
                max_tokens=self.max_context,
                temperature=getattr(self, "_vb_temperature", 1.0),
                top_k=getattr(self, "_vb_top_k", 50),
                stop=["<|SPEECH_GENERATION_END|>"],
            )
            return output["choices"][0]["text"]

        instance._vb_temperature = temperature
        instance._vb_top_k = top_k
        # Bind la méthode patchée à l'instance
        import types
        instance._infer_ggml = types.MethodType(patched, instance)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("monkey-patch _infer_ggml échoué : %s", exc)
        return False


def _make_loader(model_key: str):
    backbone_repo, _lang, _qual = _BACKBONE_REPOS[model_key]

    def _load() -> Any:
        Cls = _NeuTTSClass()
        device = os.environ.get("VB_DEVICE", "cpu")
        instance = Cls(
            backbone_repo=backbone_repo,
            backbone_device=device,
            codec_repo=CODEC_REPO,
            codec_device=device,
        )
        # NeuTTS plafonne la génération à `self.max_context` tokens audio
        # (~50 tokens/seconde à 24 kHz). Le défaut interne (~512-1024) coupe
        # la sortie à ~10-20 s — frustrant si l'utilisateur tape un texte
        # long. On le pousse à 4096 (= ~80 s d'audio par génération).
        try:
            new_max = int(os.environ.get("VB_NEUTTS_MAX_CONTEXT", "4096"))
            if hasattr(instance, "max_context"):
                old = getattr(instance, "max_context")
                if new_max > old:
                    instance.max_context = new_max
                    log.info("NeuTTS max_context : %d → %d (%s)", old, new_max, model_key)
        except Exception:  # noqa: BLE001
            log.warning("impossible de bumper max_context sur %s", model_key)

        # Patch temperature/top_k pour plus d'expressivité prosodique. NeuTTS
        # hardcode temperature=1.0 — un peu plat. 1.1 donne un poil plus de
        # variation sans dériver de l'identité de la voix source. 1.2 a été
        # testé et trouvé "trop de pauses + tonalité éloignée" → retour à 1.1.
        # Override possible via env VB_NEUTTS_TEMPERATURE.
        try:
            temp = float(os.environ.get("VB_NEUTTS_TEMPERATURE", "1.1"))
            # top_k 120 (vs 50 défaut NeuTTS) : compromis entre diversité
            # prosodique et stabilité d'identité. 150 testé donnait un peu
            # trop de variation, 100 trop plat → 120 médiane.
            topk = int(os.environ.get("VB_NEUTTS_TOP_K", "120"))
            if _patch_infer_ggml_temperature(instance, temp, topk):
                log.info("NeuTTS expressivité : temperature=%.2f top_k=%d (%s)",
                         temp, topk, model_key)
        except Exception:  # noqa: BLE001
            pass
        return instance

    return _load


def register_loaders() -> None:
    """À appeler une fois au boot (depuis ``main.py``)."""
    for key in _BACKBONE_REPOS:
        mgr.manager.register_loader(key, _make_loader(key))


# ---------------------------------------------------------------------------
# Helpers pour les routes
# ---------------------------------------------------------------------------


def encode_reference(wav_path: Path, language: str) -> Any:
    """Encode un WAV de référence en ``ref_codes``.

    Utilise le modèle Q4 de la langue (suffisant pour l'encodage, plus rapide
    qu'avec le Q8). Le résultat doit être ``torch.save``-é par l'appelant.
    """
    key = model_key_for(language, "normal")
    tts = mgr.manager.get(key)
    return tts.encode_reference(str(wav_path))


# Constantes de trim (cf. infer ci-dessous)
NEUCODEC_TOKENS_PER_SECOND = 50      # NeuCodec produit 50 tokens audio par seconde
NEUTTS_OUTPUT_SAMPLE_RATE = 24000    # Sortie NeuTTS Air = 24 kHz mono
# Ratio théorique 480 (24000/50). On reste à la valeur stricte : le détecteur
# de silence en aval s'occupe du résidu éventuel. Tester un over-trim à 528
# rendait le système trop sensible à la détection de pauses naturelles dans
# le nouveau texte (audios coupés à 2s).
SAMPLES_PER_REF_TOKEN = NEUTTS_OUTPUT_SAMPLE_RATE // NEUCODEC_TOKENS_PER_SECOND  # 480


def _trim_leading_silence(wav, threshold: float = 0.012, window_ms: int = 20) -> "Any":
    """Trim le silence de tête en cherchant la 1re fenêtre de window_ms
    avec amplitude moyenne > threshold. Limite à 1.5s max pour ne pas
    couper de la voix par erreur.

    Sans cette étape, le trim par count de tokens (~480 samples/token) peut
    laisser un résidu de 50-300 ms de silence en début de sortie NeuTTS.
    """
    try:
        import numpy as _np
        arr = _np.abs(_np.asarray(wav))
        if arr.ndim > 1:
            arr = arr.squeeze()
        win = max(1, window_ms * NEUTTS_OUTPUT_SAMPLE_RATE // 1000)
        if len(arr) < win:
            return wav
        # Moyenne mobile via cumsum
        c = _np.cumsum(arr, dtype=_np.float64)
        means = (c[win:] - c[:-win]) / win
        voiced = _np.where(means > threshold)[0]
        if len(voiced) == 0:
            return wav
        start = int(voiced[0])
        # Cap à 1.5s pour rester safe
        max_skip = int(NEUTTS_OUTPUT_SAMPLE_RATE * 1.5)
        if start > max_skip:
            return wav
        if start > 0:
            log.info("trim leading silence: %d samples (%.2fs)",
                     start, start / NEUTTS_OUTPUT_SAMPLE_RATE)
            return wav[start:]
        return wav
    except Exception as exc:  # noqa: BLE001
        log.warning("trim leading silence skipped: %s", exc)
        return wav


def infer(text: str, ref_codes: Any, ref_text: str, language: str, quality: str,
          ref_wav_path: "Path | None" = None):
    """Synthétise un WAV complet (np.ndarray float32 à 24 kHz).

    **Workaround NeuTTS Air GGML** : le modèle ré-émet inconsisitement
    l'audio de référence en début de sortie (parfois oui, parfois non,
    parfois partiellement). Quand il le fait, c'est typiquement la fin
    de la ref qui est ré-émise sur ~2 s.

    Stratégie : si ``ref_wav_path`` est fourni, on calcule la corrélation
    d'enveloppe d'énergie entre la fin de la ref WAV et le début de
    l'output. Si la similarité dépasse un seuil → résidu présent → trim
    de ``VB_NEUTTS_HEAD_TRIM_S`` secondes (défaut 2.0). Sinon → sortie
    brute, on ne touche à rien (évite de manger le début du nouveau texte
    quand le modèle se comporte bien).
    """
    key = model_key_for(language, quality)
    tts = mgr.manager.get(key)
    output = tts.infer(text, ref_codes, ref_text)

    if ref_wav_path is not None:
        head_trim_s = float(os.environ.get("VB_NEUTTS_HEAD_TRIM_S", "2.0"))
        if head_trim_s > 0 and _has_ref_residual_at_start(output, ref_wav_path):
            try:
                trim_samples = int(head_trim_s * NEUTTS_OUTPUT_SAMPLE_RATE)
                if hasattr(output, "__len__") and len(output) > trim_samples + 1000:
                    log.info("NeuTTS : ref résiduel détecté, trim %.2fs", head_trim_s)
                    import numpy as np  # type: ignore
                    output = np.asarray(output, dtype="float32")
                    if output.ndim > 1:
                        output = output.squeeze()
                    output = output[trim_samples:]
            except Exception as exc:  # noqa: BLE001
                log.warning("trim head failed: %s", exc)
    return output


def _has_ref_residual_at_start(output, ref_wav_path,
                               tail_window_s: float = 1.0,
                               search_window_s: float = 3.0,
                               correlation_threshold: float = 0.55) -> bool:
    """Détecte si le début de ``output`` ressemble à la fin de la ref WAV
    via corrélation d'enveloppe d'énergie (RMS sur fenêtre 10 ms).

    L'enveloppe d'énergie est plus robuste que la corrélation directe sur
    waveform : deux générations du même contenu auront des waveforms
    différents (variations autorégressives) mais des enveloppes très
    similaires (mêmes attaques, mêmes pauses, même rythme prosodique).

    Retourne True si la corrélation max dépasse ``correlation_threshold``
    sur les ``search_window_s`` premières secondes de l'output.
    """
    try:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore
    except ImportError:
        return False
    try:
        ref_audio, sr = sf.read(str(ref_wav_path))
    except Exception as exc:  # noqa: BLE001
        log.warning("ref WAV illisible pour détection résidu: %s", exc)
        return False
    if sr != NEUTTS_OUTPUT_SAMPLE_RATE:
        # Le WAV de la voix devrait toujours être en 24 kHz (cf. to_wav_24k_mono)
        # mais on évite de se baser sur ça.
        return False
    if ref_audio.ndim > 1:
        ref_audio = ref_audio[:, 0]
    ref_audio = np.asarray(ref_audio, dtype=np.float32)
    out_arr = np.asarray(output, dtype=np.float32)
    if out_arr.ndim > 1:
        out_arr = out_arr.squeeze()

    tail_n = int(tail_window_s * NEUTTS_OUTPUT_SAMPLE_RATE)
    head_n = int(search_window_s * NEUTTS_OUTPUT_SAMPLE_RATE)
    if len(ref_audio) < tail_n // 2 or len(out_arr) < tail_n + 1000:
        return False
    ref_tail = ref_audio[-tail_n:] if len(ref_audio) >= tail_n else ref_audio
    out_head = out_arr[:min(len(out_arr), head_n)]

    # Enveloppe RMS à granularité 10 ms (240 samples à 24 kHz)
    win = NEUTTS_OUTPUT_SAMPLE_RATE // 100
    def _rms_env(arr):
        n = len(arr) // win
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        chunks = arr[: n * win].reshape(n, win)
        env = np.sqrt(np.mean(chunks ** 2, axis=1))
        # Normalisation pour rendre la corrélation insensible au volume
        m = float(np.max(env))
        return env / m if m > 1e-6 else env

    ref_env = _rms_env(ref_tail)
    head_env = _rms_env(out_head)
    if len(ref_env) < 5 or len(head_env) < len(ref_env):
        return False

    # Corrélation glissante normalisée (Pearson-like)
    n_pos = len(head_env) - len(ref_env) + 1
    best = 0.0
    ref_centered = ref_env - float(np.mean(ref_env))
    ref_norm = float(np.linalg.norm(ref_centered))
    if ref_norm < 1e-6:
        return False
    for i in range(n_pos):
        h = head_env[i:i + len(ref_env)]
        h_centered = h - float(np.mean(h))
        h_norm = float(np.linalg.norm(h_centered))
        if h_norm < 1e-6:
            continue
        corr = float(np.dot(ref_centered, h_centered) / (ref_norm * h_norm))
        if corr > best:
            best = corr
    log.info("NeuTTS ref-residual corr max=%.2f (seuil=%.2f)", best, correlation_threshold)
    return best >= correlation_threshold


def _trim_to_silence_end(wav,
                         search_window_s: float = 0.4,
                         silence_threshold: float = 0.008,
                         min_silence_ms: int = 120):
    """Cherche la première plage de silence (≥ ``min_silence_ms`` ms,
    amplitude < ``silence_threshold``) dans les ``search_window_s``
    premières secondes du signal, et coupe à sa fin.

    Paramètres calibrés pour distinguer la transition ref → nouveau texte
    (pause typiquement ≥ 120 ms) des pauses inter-mots naturelles dans
    le nouveau texte (typiquement < 80 ms). Window 400 ms = on ne cherche
    que dans la fenêtre où le résidu de ref pourrait être ; au-delà on
    risque de couper dans le contenu de l'utilisateur.

    Si aucun silence trouvé, retourne le signal inchangé.
    """
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return wav
    arr = np.asarray(wav, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.squeeze()
    if arr.size == 0:
        return wav

    sr = NEUTTS_OUTPUT_SAMPLE_RATE
    win = int(search_window_s * sr)
    min_sil = int(min_silence_ms * sr / 1000)
    head = arr[: win]
    if head.size < min_sil:
        return wav

    abs_head = np.abs(head)
    is_silent = abs_head < silence_threshold

    # Recherche : séquence de >= min_sil samples consécutifs silent,
    # se terminant par un sample non-silent (= reprise de la parole).
    n = len(is_silent)
    i = 0
    while i < n:
        if is_silent[i]:
            j = i
            while j < n and is_silent[j]:
                j += 1
            run_len = j - i
            if run_len >= min_sil and j < n:
                # Trouvé ! On coupe juste après la fin du silence.
                log.info("trim_to_silence_end: extra %d samples (%.2fs) trimmed",
                         j, j / sr)
                return arr[j:]
            i = j
        else:
            i += 1
    return arr


def infer_stream(text: str, ref_codes: Any, ref_text: str, language: str, quality: str):
    """Generator : yield des chunks ``np.ndarray`` float32 24 kHz mono.

    Cf. ``examples/basic_streaming_example.py`` du repo neutts-air. Permet
    de commencer la lecture côté client avant la fin de la génération
    (latence Live cible 0,6-1,4 s atteinte).
    """
    key = model_key_for(language, quality)
    tts = mgr.manager.get(key)
    yield from tts.infer_stream(text, ref_codes, ref_text)
