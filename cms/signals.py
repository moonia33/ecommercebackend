from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import (
    MegaMenuFeature, MegaMenuFeatureTranslation, Menu, MenuItem,
    MenuItemTranslation,
)


def _bump(sender, **kwargs):
    from .api import bump_nav_version
    bump_nav_version()


for _model in (
    Menu, MenuItem, MenuItemTranslation,
    MegaMenuFeature, MegaMenuFeatureTranslation,
):
    post_save.connect(_bump, sender=_model)
    post_delete.connect(_bump, sender=_model)
