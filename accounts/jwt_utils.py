from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _encode(payload: dict) -> str:
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def issue_access_token(*, user_id: int) -> str:
    exp = _now() + timedelta(minutes=int(settings.JWT_ACCESS_TTL_MINUTES))
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(_now().timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload)


def issue_refresh_token(*, user_id: int) -> str:
    exp = _now() + timedelta(days=int(settings.JWT_REFRESH_TTL_DAYS))
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(_now().timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
