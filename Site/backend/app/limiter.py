"""Limiter slowapi global, importé par les routers et par ``main.py``.

Le limiter doit être attaché à ``app.state.limiter`` côté ``main.py`` et utilisé
en décorateur côté routers : ``@limiter.limit("5/15minute")``.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
