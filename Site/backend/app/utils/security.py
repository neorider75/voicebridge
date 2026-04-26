"""Helpers sécurité : password hashing, signing sessions, lockout IP en mémoire.

Toutes les opérations sensibles passent par ce module. Pas d'utilisation de
JWT (cf. `07-security.md` — choix explicite via ``itsdangerous``).
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from passlib.hash import bcrypt

from .. import config

# ---------------------------------------------------------------------------
# Mot de passe
# ---------------------------------------------------------------------------

BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    return bcrypt.hash(plain, rounds=BCRYPT_ROUNDS)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.verify(plain, hashed)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Token API (Bearer)
# ---------------------------------------------------------------------------


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_api_token(token: str, expected_hash: str) -> bool:
    return hashlib.sha256(token.encode("utf-8")).hexdigest() == expected_hash


# ---------------------------------------------------------------------------
# Sessions (cookie ``vb_session`` signé)
# ---------------------------------------------------------------------------

SESSION_INACTIVITY_SECONDS = 8 * 3600  # 8 h
SESSION_COOKIE = "vb_session"


def _signer() -> TimestampSigner:
    secret = config.get("session_secret")
    if not secret:
        raise RuntimeError("session_secret manquant dans config.json")
    return TimestampSigner(secret, salt="vb-session")


def create_session_token(payload: str = "ok") -> str:
    """Le cookie n'a pas besoin de stocker un user_id (mono-user). On signe
    juste un marqueur ``"ok"`` avec timestamp."""
    return _signer().sign(payload).decode("utf-8")


def verify_session_token(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        _signer().unsign(raw, max_age=SESSION_INACTIVITY_SECONDS)
        return True
    except (BadSignature, SignatureExpired):
        return False


# ---------------------------------------------------------------------------
# Anti-bruteforce login
# ---------------------------------------------------------------------------

# Délai progressif après N tentatives ratées sur la même IP (compteur volant).
PROGRESSIVE_DELAY_SECONDS = {
    1: 0.0,
    2: 0.0,
    3: 2.0,
    4: 5.0,
    5: 10.0,
}

# Lockout : 10 échecs sur 1h → IP bloquée 1h.
LOCKOUT_THRESHOLD = 10
LOCKOUT_WINDOW = 3600
LOCKOUT_DURATION = 3600


@dataclass
class _IPState:
    failures: deque  # timestamps des échecs récents (dans la fenêtre)
    locked_until: float  # 0 = pas verrouillée


_lock = threading.RLock()
_ip_state: dict[str, _IPState] = defaultdict(
    lambda: _IPState(failures=deque(), locked_until=0.0)
)


def _purge_old(state: _IPState, now: float) -> None:
    while state.failures and state.failures[0] < now - LOCKOUT_WINDOW:
        state.failures.popleft()


def is_locked(ip: str) -> tuple[bool, int]:
    """Retourne ``(locked, retry_after_seconds)``."""
    now = time.time()
    with _lock:
        state = _ip_state[ip]
        if state.locked_until > now:
            return True, int(state.locked_until - now)
        return False, 0


def record_failure(ip: str) -> tuple[float, bool]:
    """Enregistre un échec login. Retourne ``(delay_seconds, now_locked)``.

    Le délai progressif est appliqué *avant* le retour de la réponse côté
    appelant (pour ralentir le brute-force).
    """
    now = time.time()
    with _lock:
        state = _ip_state[ip]
        _purge_old(state, now)
        state.failures.append(now)
        n_recent = len(state.failures)
        delay = PROGRESSIVE_DELAY_SECONDS.get(n_recent, 10.0)
        if n_recent >= LOCKOUT_THRESHOLD:
            state.locked_until = now + LOCKOUT_DURATION
            return delay, True
        return delay, False


def record_success(ip: str) -> None:
    """Reset compteur d'échecs sur login réussi."""
    with _lock:
        if ip in _ip_state:
            _ip_state[ip].failures.clear()
            _ip_state[ip].locked_until = 0.0


def remaining_attempts(ip: str) -> int:
    """Tentatives restantes avant lockout (informationnel pour l'UI)."""
    now = time.time()
    with _lock:
        state = _ip_state[ip]
        _purge_old(state, now)
        return max(0, LOCKOUT_THRESHOLD - len(state.failures))
