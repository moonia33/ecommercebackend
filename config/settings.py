"""Compatibility shim.

Use `config.settings_dev` (development) or `config.settings_prod` (production).
"""

from .settings_dev import *  # noqa: F403
