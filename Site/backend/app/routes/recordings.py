"""Routes ``/api/recordings/*``.

Cf. ``05-backend-api.md`` :
  - GET    /api/recordings              liste filtrable + tri
  - GET    /api/recordings/{id}/audio   sert le fichier
  - DELETE /api/recordings/{id}         supprime
  - POST   /api/recordings/bulk-delete  supprime plusieurs IDs
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..auth import require_auth
from ..services import recordings_store
from ..utils import files

router = APIRouter(prefix="/api/recordings", tags=["recordings"], dependencies=[Depends(require_auth)])


@router.get("")
async def list_recordings(
    mode: str = Query("all", regex="^(all|tts|stt|live)$"),
    sort: str = Query("date_desc", regex="^(date_desc|date_asc)$"),
):
    recs = recordings_store.list_recordings(mode=mode)
    recs.sort(
        key=lambda r: r.get("created_at", ""),
        reverse=(sort == "date_desc"),
    )
    total_size = sum(r.get("size_mb", 0) for r in recs)
    return {
        "recordings": recs,
        "total_count": len(recs),
        "total_size_mb": round(total_size, 2),
    }


@router.get("/{rec_id}/audio")
async def recording_audio(rec_id: str):
    rec_id = files.safe_id(rec_id)
    rec = recordings_store.get(rec_id)
    if not rec:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="enregistrement introuvable")
    fmt = rec.get("format", "wav")
    p = files.ensure_inside(config.AUDIO_DIR, recordings_store.file_path(rec_id, fmt))
    if not Path(p).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="fichier audio introuvable")
    media = "audio/mpeg" if fmt == "mp3" else "audio/wav"
    return FileResponse(p, media_type=media, filename=f"{rec_id}.{fmt}")


@router.delete("/{rec_id}")
async def delete_recording(rec_id: str):
    rec_id = files.safe_id(rec_id)
    if not recordings_store.delete(rec_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="enregistrement introuvable")
    return {"success": True}


class BulkDeletePayload(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=500)


@router.post("/bulk-delete")
async def bulk_delete_recordings(payload: BulkDeletePayload):
    """Supprime plusieurs enregistrements en une seule requête.
    Renvoie le nombre supprimé + la liste des IDs invalides/introuvables."""
    deleted = 0
    not_found: list[str] = []
    invalid: list[str] = []
    for rec_id in payload.ids:
        try:
            safe = files.safe_id(rec_id)
        except ValueError:
            invalid.append(rec_id)
            continue
        if recordings_store.delete(safe):
            deleted += 1
        else:
            not_found.append(safe)
    return {
        "success": True,
        "deleted": deleted,
        "not_found": not_found,
        "invalid": invalid,
    }
