"""Routes ``/api/rvc/*`` — gestion des modèles RVC utilisateur.

Endpoints :
- ``GET    /api/rvc/models``               : liste tous les modèles RVC
- ``GET    /api/rvc/models/{id}``          : détail d'un modèle
- ``POST   /api/rvc/models``               : upload .pth + .index (multipart)
                                             → retourne task_id pour suivi upload
- ``DELETE /api/rvc/models/{id}``          : supprime (local + RunPod Volume)
- ``POST   /api/rvc/models/{id}/test``     : lance un test rapide via worker
                                             → retourne task_id pour suivi
- ``GET    /api/rvc/models/{id}/test_audio``: audio WAV de test (si disponible)

Pattern progression UX (Décision sys.) : tout ce qui dépasse 1s expose un
task_id, le client se branche sur ``/ws/progress/{task_id}``.
"""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile, status)
from fastapi.responses import FileResponse

from .. import config
from ..auth import require_auth
from ..limiter import limiter
from ..services import (progress_tasks, rvc_models_store, runpod_client,
                         voices_store)
from ..utils import files

router = APIRouter(prefix="/api/rvc", tags=["rvc"],
                   dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.rvc")


# ════════════════════════════════════════════════════════════════════
# GET liste / détail
# ════════════════════════════════════════════════════════════════════


@router.get("/models")
async def list_models() -> dict:
    return {"models": rvc_models_store.list_models()}


@router.get("/models/{model_id}")
async def get_model(model_id: str) -> dict:
    m = rvc_models_store.get(model_id)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="modèle RVC introuvable")
    return m


@router.get("/models/{model_id}/test_audio")
async def model_test_audio(model_id: str):
    """Récupère l'audio de test généré à l'import (si présent)."""
    m = rvc_models_store.get(model_id)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="modèle RVC introuvable")
    p = rvc_models_store.test_audio_path(model_id)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="audio de test pas encore généré")
    return FileResponse(p, media_type="audio/wav", filename=f"{model_id}_test.wav")


# ════════════════════════════════════════════════════════════════════
# POST upload (.pth + .index → S3 RunPod en background)
# ════════════════════════════════════════════════════════════════════


@router.post("/models", status_code=202)
@limiter.limit("5/minute")
async def upload_model(
    request: Request,
    pth_file: UploadFile = File(...),
    index_file: UploadFile | None = File(None),
    name: str = Form(..., min_length=1, max_length=80),
    description: str = Form(default=""),
    voice_id: str | None = Form(default=None),
):
    """Upload un modèle RVC (.pth + .index optionnel).

    Pipeline (asynchrone via task_id) :
    1. Validation .pth (magic bytes, structure PyTorch, sample_rate)
    2. Validation .index (taille, magic bytes)
    3. Upload S3 vers RunPod Network Volume avec progression
    4. Mise à jour metadata côté Hostinger (status: active)

    Retourne immédiatement le task_id ; le client se branche sur
    ``/ws/progress/{task_id}`` pour suivre.
    """
    if not runpod_client.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "error": "runpod_not_configured",
            "message": "RunPod doit être configuré pour uploader un modèle RVC. "
                       "Allez dans Réglages → Cloud.",
        })

    # voice_id optionnel — vérifie qu'il existe si fourni
    if voice_id:
        try:
            voice_id = files.safe_id(voice_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="voice_id invalide")
        if not voices_store.get(voice_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail=f"voice_id {voice_id!r} introuvable")

    # ── Persiste les uploads dans tmp pour validation + upload ──
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    pth_tmp = NamedTemporaryFile(delete=False, dir=config.TMP_DIR,
                                  suffix=".pth")
    pth_tmp_path = Path(pth_tmp.name)
    shutil.copyfileobj(pth_file.file, pth_tmp)
    pth_tmp.close()

    index_tmp_path = None
    if index_file is not None and index_file.filename:
        idx = NamedTemporaryFile(delete=False, dir=config.TMP_DIR,
                                  suffix=".index")
        index_tmp_path = Path(idx.name)
        shutil.copyfileobj(index_file.file, idx)
        idx.close()

    # ── Validation synchrone (~1-2s, OK dans la requête) ──
    try:
        info = rvc_models_store.validate_pth_file(pth_tmp_path)
        if index_tmp_path:
            rvc_models_store.validate_index_file(index_tmp_path)
    except ValueError as exc:
        pth_tmp_path.unlink(missing_ok=True)
        if index_tmp_path:
            index_tmp_path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # ── Crée la metadata + task_id ──
    model_id = files.new_id("rvc_")
    size_mb = round(pth_tmp_path.stat().st_size / 1e6, 1)

    rvc_models_store.add({
        "id": model_id,
        "name": name.strip(),
        "description": description.strip(),
        "voice_id": voice_id,
        "sample_rate": info["sample_rate"],
        "f0": info["f0"],
        "version": info["version"],
        "size_mb": size_mb,
        "status": "uploading",
        "runpod_volume_path": f"rvc_models/{model_id}/",
    })

    task_id = progress_tasks.create(
        kind="rvc_upload",
        details={"model_id": model_id, "name": name, "size_mb": size_mb},
    )

    # ── Lance l'upload S3 en background ──
    update = progress_tasks.updater(task_id)
    threading.Thread(
        target=_run_upload,
        args=(model_id, pth_tmp_path, index_tmp_path, update),
        daemon=True,
    ).start()

    log.info("rvc model upload queued id=%s task=%s size=%sMo",
             model_id, task_id, size_mb)
    return {"model_id": model_id, "task_id": task_id, "status": "uploading"}


def _run_upload(model_id: str, pth_path: Path, index_path: Path | None,
                update) -> None:
    """Background worker : upload S3 → metadata.

    Tourne dans un thread non-async ; c'est OK car boto3 est sync et
    progress_tasks est thread-safe.
    """
    try:
        update(status="running", progress=5,
               step="Préparation upload RunPod (.pth)")
        pth_size = pth_path.stat().st_size

        def pth_progress(bytes_uploaded):
            # 5% → 70% pour le .pth
            pct = 5 + int(65 * bytes_uploaded / max(pth_size, 1))
            update(progress=pct,
                   step=f"Upload .pth {bytes_uploaded // (1024 * 1024)}/"
                        f"{pth_size // (1024 * 1024)} Mo")

        runpod_client.upload_file(
            local_path=str(pth_path),
            remote_key=rvc_models_store.runpod_pth_key(model_id),
            progress_cb=pth_progress,
        )

        if index_path and index_path.exists():
            update(progress=72, step="Upload .index FAISS")
            idx_size = index_path.stat().st_size

            def idx_progress(bytes_uploaded):
                # 72% → 95%
                pct = 72 + int(23 * bytes_uploaded / max(idx_size, 1))
                update(progress=pct,
                       step=f"Upload .index {bytes_uploaded // (1024 * 1024)}/"
                            f"{idx_size // (1024 * 1024)} Mo")

            runpod_client.upload_file(
                local_path=str(index_path),
                remote_key=rvc_models_store.runpod_index_key(model_id),
                progress_cb=idx_progress,
            )

        update(progress=98, step="Finalisation")
        rvc_models_store.patch(model_id, {"status": "active"})

        update(status="done", progress=100, step="Modèle prêt à l'emploi",
               result={"model_id": model_id})
        log.info("rvc model %s upload OK", model_id)

    except Exception as exc:  # noqa: BLE001
        log.exception("rvc upload failed model=%s", model_id)
        rvc_models_store.patch(model_id, {
            "status": "failed",
            "error_message": str(exc)[:300],
        })
        update(status="error", error=str(exc))
    finally:
        pth_path.unlink(missing_ok=True)
        if index_path:
            index_path.unlink(missing_ok=True)


# ════════════════════════════════════════════════════════════════════
# DELETE (Hostinger metadata + RunPod Volume cleanup)
# ════════════════════════════════════════════════════════════════════


@router.delete("/models/{model_id}")
async def delete_model(model_id: str) -> dict:
    m = rvc_models_store.get(model_id)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="modèle introuvable")

    # Supprime les fichiers RunPod Volume (best-effort)
    if runpod_client.is_configured():
        try:
            runpod_client.delete_object(rvc_models_store.runpod_pth_key(model_id))
            runpod_client.delete_object(rvc_models_store.runpod_index_key(model_id))
        except runpod_client.RunPodError as exc:
            log.warning("RunPod cleanup failed for %s: %s", model_id, exc)

    # Supprime metadata + dossier local (test audio etc.)
    rvc_models_store.delete(model_id)
    md = rvc_models_store.model_dir(model_id)
    if md.exists():
        shutil.rmtree(md, ignore_errors=True)

    log.info("rvc model deleted id=%s", model_id)
    return {"success": True}


# ════════════════════════════════════════════════════════════════════
# POST test (génère un audio rapide via worker)
# ════════════════════════════════════════════════════════════════════


@router.post("/models/{model_id}/test", status_code=202)
@limiter.limit("10/minute")
async def test_model(request: Request, model_id: str) -> dict:
    """Lance une conversion rapide via le worker pour valider le modèle.

    Asynchrone via task_id. À la fin, l'audio sample est stocké dans
    ``data/rvc_models/{id}/sample_test.wav``.
    """
    m = rvc_models_store.get(model_id)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="modèle introuvable")
    if m.get("status") != "active":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"modèle pas prêt (status={m.get('status')})")

    if not runpod_client.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="RunPod non configuré")

    task_id = progress_tasks.create(
        kind="rvc_test",
        details={"model_id": model_id},
    )
    update = progress_tasks.updater(task_id)

    threading.Thread(
        target=_run_test,
        args=(model_id, update),
        daemon=True,
    ).start()

    return {"task_id": task_id}


def _run_test(model_id: str, update) -> None:
    """Background : appelle worker rvc_convert avec un sample générique."""
    import base64

    try:
        update(status="running", progress=10, step="Préparation sample audio")

        # On envoie un audio nul de 2s comme placeholder ; idéalement on
        # devrait avoir un vrai sample neutre stocké dans le worker.
        # Pour V3.0 : on attend un vrai sample provided par l'utilisateur
        # ou pré-positionné. Ici on log et on no-op gracieusement.
        # TODO Phase F : sample test dans /runpod-volume/test_samples/
        update(progress=30, step="Appel worker rvc_convert")

        # Placeholder : 2s de silence à 24kHz (suffisant pour valider le
        # chemin RunPod sans surconsommer du GPU)
        import io
        try:
            import numpy as np  # type: ignore
            import soundfile as sf  # type: ignore
            silence = np.zeros(48000, dtype=np.float32)
            buf = io.BytesIO()
            sf.write(buf, silence, 24000, format="WAV", subtype="PCM_16")
            audio_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            update(status="error", error=f"audio prep failed: {exc}")
            return

        result = runpod_client.runsync({
            "operation": "rvc_convert",
            "rvc_model_id": model_id,
            "audio": audio_b64,
            "pitch_shift": 0,
            "index_rate": 0.7,
        }, timeout=120.0)

        update(progress=90, step="Sauvegarde audio test")
        out_b64 = result.get("audio")
        if out_b64:
            md = rvc_models_store.model_dir(model_id)
            md.mkdir(parents=True, exist_ok=True)
            wav_bytes = base64.b64decode(out_b64)
            rvc_models_store.test_audio_path(model_id).write_bytes(wav_bytes)

        update(status="done", progress=100, step="Test OK",
               result={"audio_path": str(rvc_models_store.test_audio_path(model_id))})

    except Exception as exc:  # noqa: BLE001
        log.exception("rvc test failed model=%s", model_id)
        update(status="error", error=str(exc))
