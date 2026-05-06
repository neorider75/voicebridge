"""Retraitement audio pour préparer un dataset RVC à partir des enregistrements
bruts du wizard ``/recording-session``.

Pipeline (toutes les étapes émettent un progress_cb pour la barre UX) :
1. Concaténation des 5 blocs en un WAV global
2. Détection des régions de parole (Silero VAD)
3. Découpage en clips de 5-15s aux frontières de silence
4. Suppression du bruit de fond (noisereduce)
5. Normalisation peak à -3 dB
6. Calcul score qualité (SNR, distribution durées, niveaux)
7. Export WAV 44.1kHz + manifest.json

Tourne sur Hostinger CPU (asynchrone, ~5 min pour 20 min audio).

Cf. spec ``Spec/voicebridge_v3_specs/Spec/voicebridge_specs/12-rvc-pipeline.md``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("voicebridge.dataset_processor")

# Callback signature : update(progress_percent, step_str, details_dict)
ProgressCallback = Callable[[float, str, Optional[dict]], None]

OUTPUT_SAMPLE_RATE = 44100   # Standard Kaggle RVC
TARGET_PEAK_DB = -3.0


@dataclass
class ClipMetadata:
    filename: str
    duration_s: float
    snr_db: float
    peak_db: float
    rms_db: float
    block_origin: int


@dataclass
class QualityReport:
    total_clips: int
    total_duration_s: float
    snr_avg_db: float
    snr_min_db: float
    peak_avg_db: float
    duration_distribution: dict
    score: int


# ────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────


def process_session(
    session_dir: Path,
    progress_cb: ProgressCallback,
    denoise_strength: float = 0.7,
    min_clip_seconds: float = 5.0,
    max_clip_seconds: float = 15.0,
) -> dict:
    """Pipeline complet. Appelle ``progress_cb(percent, step, details)``.

    Args:
        session_dir: dossier ``data/recording_sessions/{session_id}/``
        progress_cb: callback de progression UX
        denoise_strength: 0.0 (off) à 1.0 (max)
        min_clip_seconds: clips plus courts ignorés
        max_clip_seconds: clips plus longs découpés

    Returns:
        ``{clips_count, total_duration_s, score, output_dir}``
    """
    try:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(f"numpy/soundfile manquants : {exc}") from exc

    output_dir = session_dir / "processed"
    output_dir.mkdir(exist_ok=True, parents=True)

    progress_cb(5, "Chargement des blocs", None)
    raw_blocks = _load_raw_blocks(session_dir)
    if not raw_blocks:
        raise RuntimeError("Aucun bloc audio trouvé dans la session")

    progress_cb(15, "Concaténation", None)
    full_audio, sr = _concatenate_blocks(raw_blocks)

    progress_cb(25, "Détection des régions de parole (Silero VAD)", None)
    speech_regions = _detect_speech_regions(full_audio, sr)
    log.info("VAD: %d speech regions found", len(speech_regions))

    progress_cb(40, "Découpage en clips", None)
    clips = _segment_clips(full_audio, sr, speech_regions,
                           min_clip_seconds, max_clip_seconds)
    progress_cb(45, f"{len(clips)} clips identifiés",
                {"clips_count": len(clips)})

    if not clips:
        raise RuntimeError("Aucun clip valide après segmentation "
                           "(audio trop court ou trop bruyant)")

    progress_cb(50, "Suppression du bruit (noisereduce)", None)
    n = len(clips)

    def _denoise_progress(i, total):
        progress_cb(50 + int(20 * i / total),
                    f"Débruit clip {i + 1}/{total}", None)

    clips_denoised = _denoise_clips(clips, sr, denoise_strength,
                                     _denoise_progress)

    progress_cb(75, "Normalisation peak", None)
    clips_normalized = _normalize_clips(clips_denoised)

    progress_cb(85, "Calcul du score qualité", None)
    metadata, report = _compute_quality_report(clips_normalized, sr)

    progress_cb(92, "Export des fichiers", None)
    _export_clips(output_dir, clips_normalized, sr, metadata)
    _export_manifest(output_dir, metadata, report)

    progress_cb(100, "Terminé", {"score": report.score,
                                  "clips_count": len(clips_normalized)})

    return {
        "clips_count": len(clips_normalized),
        "total_duration_s": report.total_duration_s,
        "score": report.score,
        "output_dir": str(output_dir),
    }


# ────────────────────────────────────────────────────────────────────
# Pipeline steps
# ────────────────────────────────────────────────────────────────────


def _load_raw_blocks(session_dir: Path) -> list[tuple]:
    """Retourne ``[(audio_array, sample_rate, block_idx), ...]``."""
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore
    blocks = []
    for i in range(1, 6):
        path = session_dir / f"block_{i}_raw.wav"
        if not path.exists():
            log.warning("Bloc %d absent : %s", i, path)
            continue
        audio, sr = sf.read(path)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        blocks.append((audio.astype(np.float32), int(sr), i))
    return blocks


def _concatenate_blocks(blocks: list) -> tuple:
    """Concatène les blocs et garde leur origine.

    Retourne (audio_array, sample_rate). Block origin tracking simplifié :
    on retient l'index du premier bloc (suffisant pour les statistiques).
    """
    import numpy as np  # type: ignore
    if not blocks:
        return np.array([], dtype=np.float32), 16000
    sr = blocks[0][1]
    audio = np.concatenate([b[0] for b in blocks])
    return audio, sr


def _detect_speech_regions(audio, sr: int) -> list[tuple[int, int]]:
    """Silero VAD pour identifier les régions de parole.

    Returns: list of (start_sample, end_sample) at the original ``sr``.
    """
    import numpy as np  # type: ignore
    import torch  # type: ignore

    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
    )
    get_speech_timestamps = utils[0]

    audio_t = torch.from_numpy(audio.astype(np.float32))
    if sr != 16000:
        try:
            import torchaudio.functional as TF  # type: ignore
            audio_t = TF.resample(audio_t, sr, 16000)
        except Exception:  # noqa: BLE001
            ratio = 16000 / sr
            new_len = int(len(audio) * ratio)
            x_old = np.linspace(0, 1, len(audio), endpoint=False)
            x_new = np.linspace(0, 1, new_len, endpoint=False)
            audio_t = torch.from_numpy(np.interp(x_new, x_old, audio).astype(np.float32))

    timestamps = get_speech_timestamps(
        audio_t, model,
        sampling_rate=16000,
        min_speech_duration_ms=500,
        min_silence_duration_ms=300,
    )
    factor = sr / 16000
    return [(int(t["start"] * factor), int(t["end"] * factor))
            for t in timestamps]


def _segment_clips(audio, sr: int, regions: list,
                   min_s: float, max_s: float) -> list[tuple]:
    """Découpe en clips. Retourne ``[(clip_audio, block_origin), ...]``."""
    import numpy as np  # type: ignore  # noqa: F401
    clips = []
    for start, end in regions:
        duration = (end - start) / sr
        if duration < min_s:
            continue
        if duration <= max_s:
            clips.append((audio[start:end], 1))
        else:
            n_subclips = int((duration + max_s - 1) // max_s)
            sub_duration = (end - start) // n_subclips
            for i in range(n_subclips):
                sub_start = start + i * sub_duration
                sub_end = min(sub_start + sub_duration, end)
                if (sub_end - sub_start) / sr >= min_s:
                    clips.append((audio[sub_start:sub_end], 1))
    return clips


def _denoise_clips(clips: list, sr: int, strength: float,
                   progress_cb: Callable[[int, int], None] | None = None) -> list:
    """Applique noisereduce sur chaque clip."""
    try:
        import noisereduce as nr  # type: ignore
    except ImportError:
        log.warning("noisereduce non installé — skip denoising")
        return clips

    out = []
    n = len(clips)
    for i, (clip, origin) in enumerate(clips):
        try:
            denoised = nr.reduce_noise(y=clip, sr=sr, prop_decrease=strength)
            out.append((denoised, origin))
        except Exception as exc:  # noqa: BLE001
            log.warning("Denoise clip %d failed (%s), kept raw", i, exc)
            out.append((clip, origin))
        if progress_cb:
            progress_cb(i, n)
    return out


def _normalize_clips(clips: list) -> list:
    """Normalise tous les clips à TARGET_PEAK_DB."""
    import numpy as np  # type: ignore
    out = []
    target_linear = 10 ** (TARGET_PEAK_DB / 20)
    for clip, origin in clips:
        peak = float(np.max(np.abs(clip)))
        if peak > 1e-6:
            clip = clip * (target_linear / peak)
        out.append((clip, origin))
    return out


def _compute_quality_report(clips: list, sr: int) -> tuple:
    """Calcule métadonnées + score qualité 0-100."""
    import numpy as np  # type: ignore
    metadata: list[ClipMetadata] = []
    snrs, durations, peaks = [], [], []

    for i, (clip, origin) in enumerate(clips):
        duration = len(clip) / sr
        peak = 20 * float(np.log10(np.max(np.abs(clip)) + 1e-10))
        rms = 20 * float(np.log10(np.sqrt(np.mean(clip ** 2)) + 1e-10))
        snr = _estimate_snr(clip)

        metadata.append(ClipMetadata(
            filename=f"clip_{i + 1:03d}.wav",
            duration_s=round(duration, 2),
            snr_db=round(snr, 1),
            peak_db=round(peak, 1),
            rms_db=round(rms, 1),
            block_origin=origin,
        ))
        snrs.append(snr)
        durations.append(duration)
        peaks.append(peak)

    dist = {"<5s": 0, "5-10s": 0, "10-15s": 0, ">15s": 0}
    for d in durations:
        if d < 5:
            dist["<5s"] += 1
        elif d < 10:
            dist["5-10s"] += 1
        elif d < 15:
            dist["10-15s"] += 1
        else:
            dist[">15s"] += 1

    snr_avg = float(np.mean(snrs)) if snrs else 0.0
    score_snr = min(40, int(snr_avg * 1.3))
    score_count = min(30, int(len(clips) / 5))
    well_distributed = (dist["5-10s"] + dist["10-15s"]) >= len(clips) * 0.7
    score_dist = 30 if well_distributed else 15
    total = min(100, score_snr + score_count + score_dist)

    report = QualityReport(
        total_clips=len(clips),
        total_duration_s=round(float(sum(durations)), 1),
        snr_avg_db=round(snr_avg, 1),
        snr_min_db=round(float(min(snrs)), 1) if snrs else 0.0,
        peak_avg_db=round(float(np.mean(peaks)), 1) if peaks else 0.0,
        duration_distribution=dist,
        score=total,
    )
    return metadata, report


def _estimate_snr(clip) -> float:
    """SNR approximatif : ratio percentile 95 / percentile 5 de |clip|."""
    import numpy as np  # type: ignore
    abs_clip = np.abs(clip)
    if len(abs_clip) == 0:
        return 0.0
    signal = float(np.percentile(abs_clip, 95))
    noise = float(np.percentile(abs_clip, 5))
    if noise < 1e-10:
        return 60.0
    return 20 * float(np.log10(signal / noise))


def _export_clips(output_dir: Path, clips: list, sr_in: int,
                  metadata: list[ClipMetadata]) -> None:
    """Exporte les clips en WAV 44.1kHz mono PCM 16-bit (format Kaggle)."""
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore

    for (clip, _), meta in zip(clips, metadata):
        # Resample to OUTPUT_SAMPLE_RATE si nécessaire
        if sr_in != OUTPUT_SAMPLE_RATE:
            ratio = OUTPUT_SAMPLE_RATE / sr_in
            new_len = int(len(clip) * ratio)
            x_old = np.linspace(0, 1, len(clip), endpoint=False)
            x_new = np.linspace(0, 1, new_len, endpoint=False)
            clip = np.interp(x_new, x_old, clip).astype(np.float32)
        sf.write(output_dir / meta.filename, clip, OUTPUT_SAMPLE_RATE,
                 format="WAV", subtype="PCM_16")


def _export_manifest(output_dir: Path, metadata: list[ClipMetadata],
                     report: QualityReport) -> None:
    manifest = {
        "version": "1.0",
        "sample_rate": OUTPUT_SAMPLE_RATE,
        "clips": [asdict(m) for m in metadata],
        "quality_report": asdict(report),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)
    )


# ────────────────────────────────────────────────────────────────────
# ZIP export (pour upload Kaggle)
# ────────────────────────────────────────────────────────────────────


def export_zip(processed_dir: Path, zip_path: Path) -> Path:
    """Crée un ZIP du dossier ``processed/`` (clips + manifest)."""
    import zipfile

    if not processed_dir.exists():
        raise RuntimeError(f"Dossier processed introuvable : {processed_dir}")

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(processed_dir.iterdir()):
            if f.is_file():
                zf.write(f, arcname=f.name)
    log.info("ZIP export → %s (%.1f Mo)",
             zip_path, zip_path.stat().st_size / 1e6)
    return zip_path
