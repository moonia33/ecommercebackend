from __future__ import annotations

from django.contrib.auth import get_user_model
from django.conf import settings
from ninja.security import HttpBearer

from .jwt_utils import decode_token

User = get_user_model()


class JWTAuth(HttpBearer):
    def __call__(self, request):
        # Cookie-only auth: access token is stored in HttpOnly cookie.
        try:
            cookie_name = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", "access_token")
            token = (request.COOKIES.get(cookie_name) or "").strip()
        except Exception:
            token = ""

        if not token:
            return None

        return self.authenticate(request, token)

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
