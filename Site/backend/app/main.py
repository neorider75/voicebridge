"""Point d'entrée FastAPI VoiceBridge.

Lancement local (dev) :
    uvicorn app.main:app --host 127.0.0.1 --port 8000

En production : géré par ``voicebridge.service`` (systemd) sur le VPS.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from . import auth as auth_mod
from . import config
from .limiter import limiter
from .models import stt as stt_module
from .models import tts as tts_module
from .models import vad as vad_module
from .routes import auth as r_auth
from .routes import detection as r_detection
from .routes import live as r_live
from .routes import recordings as r_recordings
from .routes import settings as r_settings
from .routes import stt as r_stt
from .routes import system as r_system
from .routes import tts as r_tts
from .routes import voices as r_voices

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = config.LOGS_DIR
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

_log_format = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(
    level=os.environ.get("VB_LOG_LEVEL", "INFO"),
    format=_log_format,
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("voicebridge")

# ---------------------------------------------------------------------------
# App + rate limiter
# ---------------------------------------------------------------------------

app = FastAPI(title="VoiceBridge", version="1.0.0-poc", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Enregistre les factory functions des modèles ML auprès du manager.
# (lazy : rien n'est chargé tant qu'aucune route ne les demande)
try:
    tts_module.register_loaders()
except Exception:  # noqa: BLE001
    log.warning("register_loaders TTS impossible (deps ML manquantes ?)")

try:
    stt_module.register_loaders()
except Exception:  # noqa: BLE001
    log.warning("register_loaders STT impossible (deps ML manquantes ?)")

try:
    vad_module.register_loaders()
except Exception:  # noqa: BLE001
    log.warning("register_loaders VAD impossible (deps ML manquantes ?)")


# ---------------------------------------------------------------------------
# Middleware : headers de sécurité + auth
# ---------------------------------------------------------------------------

# TODO (avant POC en prod) : retirer 'unsafe-inline' de style-src en éclatant
# tous les style="..." restants dans des fichiers CSS dédiés.
CSP = (
    "default-src 'self'; "
    "style-src 'self' 'unsafe-inline' fonts.googleapis.com; "
    "font-src 'self' fonts.gstatic.com; "
    "script-src 'self'; "
    "img-src 'self' data:; "
    "media-src 'self' blob:; "
    "connect-src 'self' wss: https:"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    return response


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path

    # Public : login API, status, healthz, statics, /login (page)
    if auth_mod.is_public_path(path):
        return await call_next(request)

    if auth_mod.is_authenticated(request):
        return await call_next(request)

    # /api/* non authentifié → 401 JSON
    if path.startswith("/api/"):
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "Authentification requise"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Page → redirect /login
    return RedirectResponse(url="/login", status_code=302)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(r_auth.router)
app.include_router(r_system.router)
app.include_router(r_voices.router)
app.include_router(r_tts.router)
app.include_router(r_stt.router)
app.include_router(r_recordings.router)
app.include_router(r_detection.router)
app.include_router(r_settings.router)
app.include_router(r_live.router)


# ---------------------------------------------------------------------------
# Frontend statique
# ---------------------------------------------------------------------------

# Le frontend est servi depuis ``/var/voicebridge/app/frontend`` en prod,
# ou depuis ``$VB_FRONTEND_DIR`` (chemin relatif au repo) en dev.
FRONTEND_DIR = Path(
    os.environ.get(
        "VB_FRONTEND_DIR",
        str(Path(__file__).resolve().parent.parent.parent / "frontend"),
    )
)

if FRONTEND_DIR.exists():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


def _serve_html(filename: str) -> Response:
    fp = FRONTEND_DIR / filename
    if not fp.exists():
        return JSONResponse(
            status_code=404, content={"error": "not_found", "message": "Page introuvable"}
        )
    return FileResponse(fp, media_type="text/html; charset=utf-8")


@app.get("/login")
async def page_login():
    return _serve_html("login.html")


@app.get("/")
async def page_root(request: Request):
    if not auth_mod.is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return _serve_html("index.html")


# Pages applicatives — servies uniquement si authentifié (middleware ``auth_gate``)
_HTML_PAGES = {
    "/studio": "studio.html",
    "/voices": "voices.html",
    "/voices/new": "voices-new.html",
    "/recordings": "recordings.html",
    "/detection": "detection.html",
    "/settings": "settings.html",
}

for _path, _file in _HTML_PAGES.items():
    def _make_handler(filename: str):
        async def _handler():
            return _serve_html(filename)
        return _handler
    app.add_api_route(_path, _make_handler(_file), methods=["GET"], include_in_schema=False)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/favicon.ico")
async def favicon():
    fp = FRONTEND_DIR / "assets" / "favicon.ico"
    if fp.exists():
        return FileResponse(fp)
    return Response(status_code=204)
