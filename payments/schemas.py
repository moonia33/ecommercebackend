from __future__ import annotations

from ninja import Schema


class NeopayCallbackIn(Schema):
    token: str
