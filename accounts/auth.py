from __future__ import annotations

from django.contrib.auth import get_user_model
from ninja.security import HttpBearer

from .jwt_utils import decode_token

User = get_user_model()


class JWTAuth(HttpBearer):
    def authenticate(self, request, token: str):
        try:
            payload = decode_token(token)
        except Exception:
            return None

        if payload.get("type") != "access":
            return None

        user_id = payload.get("sub")
        if not user_id:
            return None

        try:
            return User.objects.get(id=int(user_id), is_active=True)
        except User.DoesNotExist:
            return None
