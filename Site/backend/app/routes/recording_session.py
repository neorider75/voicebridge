"""Routes ``/api/recording_session/*`` — wizard d'enregistrement RVC.

Workflow utilisateur (Phase 1-3 du pipeline RVC, cf. doc 12) :

1. POST /api/recording_session/create               → session_id + 5 blocs
2. POST /api/recording_session/{id}/append_chunk    → PCM binaire (par bloc)
3. POST /api/recording_session/{id}/finish_block    → ferme un bloc
4. POST /api/recording_session/{id}/process         → lance le retraitement
                                                       async (task_id)
5. GET  /api/recording_session/{id}/processed       → liste clips + score
6. GET  /api/recording_session/{id}/clip/{n}/audio  → lecture WAV d'un clip
7. DELETE /api/recording_session/{id}/clip/{n}      → supprime un clip
8. GET  /api/recording_session/{id}/export          → ZIP prêt pour Kaggle
9. DELETE /api/recording_session/{id}               → cleanup complet

Stockage temporaire ``data/recording_sessions/{id}/`` :

    session.json          # metadata (name, lang, blocks_state)
    block_1_raw.wav       # PCM 16 kHz mono brut concat des chunks
    block_2_raw.wav
    ...
    processed/
      clip_001.wav        # 44.1 kHz mono PCM 16 (format Kaggle)
      ...
      manifest.json
      quality_report.json
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import threading
import wave
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (APIRouter, Depends, HTTPException, Query, Request,
                     status)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..auth import require_auth
from ..limiter import limiter
from ..services import audio_dataset_processor, progress_tasks
from ..utils import files

router = APIRouter(prefix="/api/recording_session", tags=["recording-session"],
                   dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.recording_session")

SESSION_SAMPLE_RATE = 16000
NUM_BLOCKS = 5
MAX_BLOCK_SECONDS = 600          # 10 min max par bloc
MAX_TOTAL_SECONDS = 30 * 60      # 30 min total dataset


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


def _sessions_dir() -> Path:
    return config.DATA_DIR / "recording_sessions"


def _session_dir(session_id: str) -> Path:
    return _sessions_dir() / files.safe_id(session_id)


def _block_path(session_id: str, block: int) -> Path:
    return _session_dir(session_id) / f"block_{block}_raw.wav"


def _meta_path(session_id: str) -> Path:
    return _session_dir(session_id) / "session.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_meta(session_id: str) -> dict | None:
    p = _meta_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _save_meta(session_id: str, meta: dict) -> None:
    p = _meta_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    tmp.replace(p)


def _wav_duration_seconds(wav_path: Path) -> float:
    if not wav_path.exists():
        return 0.0
    try:
        with wave.open(str(wav_path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate() or SESSION_SAMPLE_RATE)
    except Exception:  # noqa: BLE001
        return 0.0


def _append_pcm_to_wav(wav_path: Path, pcm_bytes: bytes,
                       sample_rate: int = SESSION_SAMPLE_RATE) -> None:
    """Append du PCM 16-bit mono à un WAV existant (ou crée un nouveau).

    Implémentation simple : on relit l'existant + concat + réécrit.
    Pour ~10 min de bloc à 16 kHz mono PCM = ~19 Mo, c'est OK.
    """
    if not pcm_bytes:
        return
    if wav_path.exists():
        with wave.open(str(wav_path), "rb") as wf:
            existing = wf.readframes(wf.getnframes())
        combined = existing + pcm_bytes
    else:
        combined = pcm_bytes
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(combined)


# ════════════════════════════════════════════════════════════════════
# POST create
# ════════════════════════════════════════════════════════════════════


class CreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    language: str = Field(default="fr", min_length=2, max_length=5)


@router.post("/create", status_code=201)
@limiter.limit("10/minute")
async def create_session(request: Request, payload: CreatePayload) -> dict:
    session_id = files.new_id("rec_")
    meta = {
        "id": session_id,
        "name": payload.name.strip(),
        "language": payload.language,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "blocks": {str(i): {"completed": False, "duration_s": 0.0}
                   for i in range(1, NUM_BLOCKS + 1)},
        "processed": False,
        "process_task_id": None,
    }
    _save_meta(session_id, meta)
    log.info("recording session created id=%s name=%r lang=%s",
             session_id, payload.name, payload.language)
    return meta


# ════════════════════════════════════════════════════════════════════
# GET / DELETE session
# ════════════════════════════════════════════════════════════════════


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict:
    meta = _load_meta(session_id)
    if not meta:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session introuvable")
    return meta


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict:
    sd = _session_dir(session_id)
    if not sd.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session introuvable")
    shutil.rmtree(sd, ignore_errors=True)
    log.info("recording session deleted id=%s", session_id)
    return {"success": True}


# ════════════════════════════════════════════════════════════════════
# POST append_chunk (PCM binaire)
# ════════════════════════════════════════════════════════════════════


@router.post("/{session_id}/append_chunk")
@limiter.limit("600/minute")  # ~10 chunks/s × 5 blocs en parallèle (large marge)
async def append_chunk(
    request: Request,
    session_id: str,
    block: int = Query(..., ge=1, le=NUM_BLOCKS),
) -> dict:
    """Append un chunk PCM 16-bit mono 16 kHz au WAV du bloc demandé.

    Body : raw bytes (Content-Type: application/octet-stream).
    """
    meta = _load_meta(session_id)
    if not meta:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session introuvable")
    if meta.get("processed"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="session déjà traitée — recréez-en une")

    raw = await request.body()
    if not raw:
        return {"appended_bytes": 0, "duration_s": 0.0}

    wav_path = _block_path(session_id, block)
    _append_pcm_to_wav(wav_path, raw)

    duration = _wav_duration_seconds(wav_path)
    if duration > MAX_BLOCK_SECONDS:
        # On n'interrompt pas mais on signale (le client peut décider d'arrêter)
        log.warning("session %s block %d > %ds (got %.1fs)",
                    session_id, block, MAX_BLOCK_SECONDS, duration)

    meta["blocks"][str(block)]["duration_s"] = round(duration, 2)
    meta["updated_at"] = _now_iso()
    _save_meta(session_id, meta)
    return {"appended_bytes": len(raw), "duration_s": round(duration, 2)}


# ════════════════════════════════════════════════════════════════════
# POST finish_block
# ════════════════════════════════════════════════════════════════════


class FinishBlockPayload(BaseModel):
    block: int = Field(..., ge=1, le=NUM_BLOCKS)


@router.post("/{session_id}/finish_block")
async def finish_block(session_id: str, payload: FinishBlockPayload) -> dict:
    meta = _load_meta(session_id)
    if not meta:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session introuvable")
    block = payload.block
    duration = _wav_duration_seconds(_block_path(session_id, block))
    meta["blocks"][str(block)] = {
        "completed": True,
        "duration_s": round(duration, 2),
    }
    meta["updated_at"] = _now_iso()
    _save_meta(session_id, meta)
    log.info("session %s block %d finished (%.1fs)", session_id, block, duration)
    return meta["blocks"][str(block)]


# ════════════════════════════════════════════════════════════════════
# POST process (lance le retraitement async + barre de progression)
# ════════════════════════════════════════════════════════════════════


class ProcessPayload(BaseModel):
    denoise_strength: float = Field(default=0.7, ge=0.0, le=1.0)
    min_clip_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    max_clip_seconds: float = Field(default=15.0, ge=2.0, le=60.0)


@router.post("/{session_id}/process", status_code=202)
@limiter.limit("5/minute")
async def process_session(
    request: Request, session_id: str, payload: ProcessPayload
) -> dict:
    meta = _load_meta(session_id)
    if not meta:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session introuvable")

    # Vérif : au moins 1 bloc avec du contenu
    sd = _session_dir(session_id)
    has_audio = any(_wav_duration_seconds(_block_path(session_id, i)) > 0
                    for i in range(1, NUM_BLOCKS + 1))
    if not has_audio:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Aucun audio enregistré dans cette session")

    task_id = progress_tasks.create(
        kind="recording_process",
        details={"session_id": session_id},
    )
    meta["process_task_id"] = task_id
    meta["updated_at"] = _now_iso()
    _save_meta(session_id, meta)

    update = progress_tasks.updater(task_id)
    threading.Thread(
        target=_run_processing,
        args=(session_id, sd, update, payload),
        daemon=True,
    ).start()

    return {"task_id": task_id, "session_id": session_id}


def _run_processing(session_id: str, session_dir: Path, update,
                     payload: ProcessPayload) -> None:
    """Background : pipeline retraitement complet."""
    try:
        update(status="running", progress=0, step="Démarrage")

        def _cb(percent, step, details):
            update(progress=percent, step=step, details=(details or {}))

        result = audio_dataset_processor.process_session(
            session_dir=session_dir,
            progress_cb=_cb,
            denoise_strength=payload.denoise_strength,
            min_clip_seconds=payload.min_clip_seconds,
            max_clip_seconds=payload.max_clip_seconds,
        )

        # Marque la session comme processed
        meta = _load_meta(session_id)
        if meta:
            meta["processed"] = True
            meta["updated_at"] = _now_iso()
            _save_meta(session_id, meta)

        update(status="done", progress=100, step="Dataset prêt à valider",
               result=result)
        log.info("session %s processed: %d clips score %d",
                 session_id, result["clips_count"], result["score"])

    except Exception as exc:  # noqa: BLE001
        log.exception("session %s processing failed", session_id)
        update(status="error", error=str(exc))


# ════════════════════════════════════════════════════════════════════
# GET processed (clips + quality report)
# ════════════════════════════════════════════════════════════════════


@router.get("/{session_id}/processed")
async def get_processed(session_id: str) -> dict:
    sd = _session_dir(session_id)
    manifest = sd / "processed" / "manifest.json"
    if not manifest.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="dataset pas encore retraité")
    return json.loads(manifest.read_text())


@router.get("/{session_id}/clip/{clip_idx}/audio")
async def get_clip_audio(session_id: str, clip_idx: int):
    sd = _session_dir(session_id)
    clip_path = sd / "processed" / f"clip_{clip_idx:03d}.wav"
    if not clip_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="clip introuvable")
    return FileResponse(clip_path, media_type="audio/wav",
                        filename=clip_path.name)


@router.delete("/{session_id}/clip/{clip_idx}")
async def delete_clip(session_id: str, clip_idx: int) -> dict:
    sd = _session_dir(session_id)
    clip_path = sd / "processed" / f"clip_{clip_idx:03d}.wav"
    manifest_path = sd / "processed" / "manifest.json"
    if not clip_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="clip introuvable")

    clip_path.unlink()
    # Mise à jour du manifest (retirer l'entrée correspondante)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        target_filename = f"clip_{clip_idx:03d}.wav"
        manifest["clips"] = [c for c in manifest.get("clips", [])
                             if c.get("filename") != target_filename]
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return {"success": True}


# ════════════════════════════════════════════════════════════════════
# GET export ZIP
# ════════════════════════════════════════════════════════════════════


@router.get("/{session_id}/export")
async def export_session(session_id: str):
    sd = _session_dir(session_id)
    processed = sd / "processed"
    if not processed.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="dataset pas encore retraité")

    zip_path = sd / f"voicebridge-rvc-{session_id}.zip"
    audio_dataset_processor.export_zip(processed, zip_path)
    return FileResponse(zip_path, media_type="application/zip",
                        filename=zip_path.name)
