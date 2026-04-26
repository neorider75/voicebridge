"""Routes ``/api/system/*`` : status (public), prechauffage, clean.

``/status`` est PUBLIC (utilisé par le polling header front avant login).
Les autres routes sont protégées par le middleware global.
"""
from __future__ import annotations

import shutil
import time

import psutil
from fastapi import APIRouter, Depends

from .. import __version__
from ..auth import require_auth

router = APIRouter(prefix="/api/system", tags=["system"])

_BOOT_TIME = time.time()


@router.get("/status")
async def status_public():
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    return {
        "version": __version__,
        "ram": {
            "used_gb": round((vm.total - vm.available) / 1e9, 2),
            "total_gb": round(vm.total / 1e9, 2),
            "percent": vm.percent,
        },
        "storage": {
            "used_gb": round(disk.used / 1e9, 2),
            "total_gb": round(disk.total / 1e9, 2),
            "percent": round(disk.used / disk.total * 100, 1),
        },
        # Les modèles ML seront cablés en livraison 2 (manager.py)
        "models": {
            "neutts_fr_q4": "unloaded",
            "neutts_en_q4": "unloaded",
            "neutts_fr_q8": "unloaded",
            "neutts_en_q8": "unloaded",
            "kyutai": "unloaded",
            "deepfake_detection_v2": "unloaded",
            "silero_vad": "unloaded",
        },
        "latency_ms": None,
        "voicebridge_connected": False,
        "uptime_seconds": int(time.time() - _BOOT_TIME),
        "status": "ready",
    }


@router.post("/prechauffage", dependencies=[Depends(require_auth)])
async def prechauffage():
    # Implémentation complète en livraison 2 (manager modèles)
    return {"success": False, "error": "not_implemented", "message": "Disponible en livraison 2"}


@router.post("/clean", dependencies=[Depends(require_auth)])
async def clean():
    # Implémentation complète en livraison 6
    return {"success": False, "error": "not_implemented", "message": "Disponible en livraison 6"}
