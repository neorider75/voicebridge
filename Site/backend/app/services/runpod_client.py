"""Client RunPod : REST API Serverless + S3 API pour les Network Volumes.

Cf. Décision 1 du document ``00-decisions-v3.md`` : upload des fichiers
(.pth RVC, voix natives, etc.) vers le Volume **via l'API S3 RunPod**
(boto3), pas via Pod éphémère ou SSH.

Configuration attendue dans ``config.json`` :

    {
      "runpod_api_key_encrypted": "gAAAAA...",
      "runpod_endpoint_id": "abc123def",
      "runpod_volume_id": "vol_xyz789",
      "runpod_datacenter": "EU-FR-1",
      "runpod_s3_access_key_encrypted": "gAAAAA...",
      "runpod_s3_secret_key_encrypted": "gAAAAA..."
    }

Les ``*_encrypted`` sont chiffrés via ``services/secrets.py``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterator, Optional

from .. import config
from . import secrets

log = logging.getLogger("voicebridge.runpod")

# Endpoints RunPod
RUNPOD_API_BASE = "https://api.runpod.ai/v2"
RUNPOD_S3_HOST_TPL = "https://s3api-{datacenter}.runpod.io"

# Timeouts par défaut (s)
DEFAULT_TIMEOUT_SYNC = 60.0      # /runsync — ex: warmup, translate
DEFAULT_TIMEOUT_STREAM = 300.0   # /run + /stream/{job_id} — pipeline live


class RunPodError(Exception):
    """Erreur générique d'appel RunPod (réseau, auth, business)."""


class RunPodNotConfiguredError(RunPodError):
    """RunPod n'est pas configuré dans config.json."""


# ────────────────────────────────────────────────────────────────────
# Lecture de la config (avec déchiffrement)
# ────────────────────────────────────────────────────────────────────


def is_configured() -> bool:
    """Retourne True si les 3 clés minimales (API key + endpoint + volume) sont set."""
    return bool(
        config.get("runpod_api_key_encrypted")
        and config.get("runpod_endpoint_id")
        and config.get("runpod_volume_id")
    )


def get_api_key() -> str:
    enc = config.get("runpod_api_key_encrypted", "")
    if not enc:
        raise RunPodNotConfiguredError("runpod_api_key_encrypted absent dans config")
    return secrets.decrypt(enc)


def get_endpoint_id() -> str:
    val = config.get("runpod_endpoint_id", "")
    if not val:
        raise RunPodNotConfiguredError("runpod_endpoint_id absent dans config")
    return val


def get_volume_id() -> str:
    val = config.get("runpod_volume_id", "")
    if not val:
        raise RunPodNotConfiguredError("runpod_volume_id absent dans config")
    return val


def get_datacenter() -> str:
    return config.get("runpod_datacenter", "EU-FR-1")


def get_s3_credentials() -> tuple[str, str]:
    """Retourne (access_key, secret_key) déchiffrés.

    Raises:
        RunPodNotConfiguredError: si les credentials S3 ne sont pas set.
    """
    enc_a = config.get("runpod_s3_access_key_encrypted", "")
    enc_s = config.get("runpod_s3_secret_key_encrypted", "")
    if not enc_a or not enc_s:
        raise RunPodNotConfiguredError(
            "runpod_s3_*_key_encrypted absents — créer les credentials S3 "
            "depuis la console RunPod (Storage → Volume → S3 Credentials)"
        )
    return secrets.decrypt(enc_a), secrets.decrypt(enc_s)


# ────────────────────────────────────────────────────────────────────
# Client REST (Serverless)
# ────────────────────────────────────────────────────────────────────


def _httpx_client(timeout: float):
    """Lazy import httpx + retourne un client HTTP/2 keep-alive."""
    try:
        import httpx  # type: ignore
    except ImportError as exc:
        raise RunPodError(
            f"httpx non installé : {exc}. pip install 'httpx[http2]>=0.27'"
        ) from exc
    return httpx.Client(
        timeout=timeout,
        http2=True,
        limits=httpx.Limits(
            max_keepalive_connections=10,
            max_connections=20,
            keepalive_expiry=300,
        ),
    )


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type": "application/json",
    }


def runsync(payload: dict, timeout: float = DEFAULT_TIMEOUT_SYNC) -> dict:
    """Appel synchrone ``/runsync/{endpoint_id}``.

    Pour les opérations courtes (warmup, translate, rvc_convert).
    RunPod attend max 5 min côté serveur.

    Args:
        payload: dict envoyé tel quel comme ``{"input": payload}``
        timeout: timeout client en secondes

    Returns:
        Le ``output`` retourné par le worker (dict).

    Raises:
        RunPodError: en cas d'échec réseau, auth, ou business (status != COMPLETED).
    """
    url = f"{RUNPOD_API_BASE}/{get_endpoint_id()}/runsync"
    body = {"input": payload}
    log.info("runpod.runsync op=%s", payload.get("operation", "?"))

    with _httpx_client(timeout) as client:
        try:
            r = client.post(url, headers=_auth_headers(), json=body)
        except Exception as exc:  # noqa: BLE001
            raise RunPodError(f"runsync HTTP failed: {exc}") from exc

    if r.status_code != 200:
        raise RunPodError(f"runsync HTTP {r.status_code}: {r.text[:300]}")

    data = r.json()
    status = data.get("status")
    if status not in ("COMPLETED", "IN_PROGRESS"):
        raise RunPodError(f"runsync status={status}: {json.dumps(data)[:300]}")

    out = data.get("output")
    if isinstance(out, dict) and "error" in out:
        raise RunPodError(f"worker error: {out.get('message') or out['error']}")
    return out if isinstance(out, dict) else {"output": out}


def run_async(payload: dict) -> str:
    """Lance un job async via ``/run/{endpoint_id}``.

    Returns:
        Le ``job_id`` retourné par RunPod (à passer à ``stream``).
    """
    url = f"{RUNPOD_API_BASE}/{get_endpoint_id()}/run"
    body = {"input": payload}
    log.info("runpod.run_async op=%s", payload.get("operation", "?"))

    with _httpx_client(DEFAULT_TIMEOUT_SYNC) as client:
        try:
            r = client.post(url, headers=_auth_headers(), json=body)
        except Exception as exc:  # noqa: BLE001
            raise RunPodError(f"run HTTP failed: {exc}") from exc

    if r.status_code != 200:
        raise RunPodError(f"run HTTP {r.status_code}: {r.text[:300]}")

    job_id = r.json().get("id")
    if not job_id:
        raise RunPodError(f"run sans job id: {r.text[:300]}")
    return job_id


def stream(job_id: str, poll_interval: float = 0.05,
           timeout: float = DEFAULT_TIMEOUT_STREAM) -> Iterator[Any]:
    """Polling ``/stream/{endpoint_id}/{job_id}`` pour les opérations en streaming.

    RunPod renvoie tous les chunks accumulés depuis le dernier polling.
    On poll toutes les ``poll_interval`` ms (50ms = compromis latence/QPS).

    Args:
        job_id: id retourné par ``run_async``
        poll_interval: délai entre 2 polls (s)
        timeout: temps total max (s)

    Yields:
        Chaque élément du flux (dict typé : ``{"type": ..., ...}``).
    """
    import time

    url = f"{RUNPOD_API_BASE}/{get_endpoint_id()}/stream/{job_id}"
    deadline = time.time() + timeout

    with _httpx_client(timeout=15.0) as client:
        while True:
            if time.time() > deadline:
                raise RunPodError(f"stream timeout après {timeout}s (job_id={job_id})")

            try:
                r = client.get(url, headers=_auth_headers())
            except Exception as exc:  # noqa: BLE001
                raise RunPodError(f"stream poll failed: {exc}") from exc

            if r.status_code != 200:
                raise RunPodError(f"stream HTTP {r.status_code}: {r.text[:200]}")

            data = r.json()
            status = data.get("status", "")
            for item in data.get("stream", []):
                payload = item.get("output")
                if payload is not None:
                    yield payload

            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                if status != "COMPLETED":
                    raise RunPodError(f"stream ended status={status}")
                return

            time.sleep(poll_interval)


# ────────────────────────────────────────────────────────────────────
# Health / status
# ────────────────────────────────────────────────────────────────────


def ping() -> dict:
    """Vérifie que l'endpoint répond. Retourne ``{ok, latency_ms, ...}``.

    N'utilise PAS de quota GPU (juste un /health-like via /run d'un job nul).
    En pratique on tente une opération unknown qui devrait retourner
    proprement une erreur ``unknown_operation``.
    """
    import time
    t0 = time.time()
    try:
        out = runsync({"operation": "_ping_"}, timeout=15.0)
        return {
            "ok": True,
            "latency_ms": int((time.time() - t0) * 1000),
            "endpoint_id": get_endpoint_id(),
            "datacenter": get_datacenter(),
            "response": out,
        }
    except RunPodError as exc:
        # Le worker a répondu "unknown_operation" → c'est un succès business
        if "unknown_operation" in str(exc):
            return {
                "ok": True,
                "latency_ms": int((time.time() - t0) * 1000),
                "endpoint_id": get_endpoint_id(),
                "datacenter": get_datacenter(),
                "note": "endpoint répond (unknown_operation attendu)",
            }
        raise


# ────────────────────────────────────────────────────────────────────
# Upload S3 (pour les .pth RVC, voix natives, etc.)
# ────────────────────────────────────────────────────────────────────


def _s3_client():
    """Construit un client boto3 ciblant l'API S3 RunPod du Volume courant."""
    try:
        import boto3  # type: ignore
        from botocore.config import Config as BotoConfig  # type: ignore
    except ImportError as exc:
        raise RunPodError(
            f"boto3 non installé : {exc}. pip install 'boto3>=1.34'"
        ) from exc

    access, secret = get_s3_credentials()
    datacenter = get_datacenter().lower()
    endpoint_url = RUNPOD_S3_HOST_TPL.format(datacenter=datacenter)

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def upload_file(
    local_path: str,
    remote_key: str,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> dict:
    """Upload un fichier local vers ``s3://{volume_id}/{remote_key}``.

    Args:
        local_path: chemin du fichier sur Hostinger
        remote_key: chemin distant (ex: ``rvc_models/abc123/model.pth``)
        progress_cb: callback ``(bytes_uploaded_total)`` appelé pendant l'upload
                     (utile pour barre de progression — Décision 1)

    Returns:
        ``{"key": remote_key, "size_bytes": int, "etag": str}``
    """
    from pathlib import Path
    p = Path(local_path)
    if not p.exists():
        raise RunPodError(f"Fichier introuvable : {local_path}")

    size_bytes = p.stat().st_size
    bucket = get_volume_id()
    log.info("runpod.upload %s → s3://%s/%s (%d bytes)",
             p.name, bucket, remote_key, size_bytes)

    client = _s3_client()

    # Callback boto3 = appelé avec (bytes_transferred_chunk) à chaque chunk
    uploaded = {"total": 0}

    def _cb(chunk_bytes: int):
        uploaded["total"] += chunk_bytes
        if progress_cb:
            progress_cb(uploaded["total"])

    try:
        client.upload_file(
            Filename=str(p),
            Bucket=bucket,
            Key=remote_key,
            Callback=_cb if progress_cb else None,
        )
    except Exception as exc:  # noqa: BLE001
        raise RunPodError(f"S3 upload échoué : {exc}") from exc

    # Récupère etag pour confirmation
    try:
        head = client.head_object(Bucket=bucket, Key=remote_key)
        etag = head.get("ETag", "").strip('"')
    except Exception:  # noqa: BLE001
        etag = ""

    return {"key": remote_key, "size_bytes": size_bytes, "etag": etag}


def delete_object(remote_key: str) -> bool:
    """Supprime un objet du Volume. Retourne True si OK."""
    bucket = get_volume_id()
    client = _s3_client()
    try:
        client.delete_object(Bucket=bucket, Key=remote_key)
        log.info("runpod.delete s3://%s/%s", bucket, remote_key)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("runpod.delete failed: %s", exc)
        return False


def list_objects(prefix: str = "") -> list[dict]:
    """Liste les objets du Volume sous un préfixe (ex: ``rvc_models/``)."""
    bucket = get_volume_id()
    client = _s3_client()
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        return [
            {"key": o["Key"], "size": o["Size"], "modified": str(o["LastModified"])}
            for o in resp.get("Contents", [])
        ]
    except Exception as exc:  # noqa: BLE001
        raise RunPodError(f"S3 list_objects échoué : {exc}") from exc
