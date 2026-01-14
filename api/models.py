from django.db import models


def get_default_site_id() -> int:
    try:
        s = Site.objects.filter(is_active=True, code="default").only("id").first()
        if s is not None:
            return int(s.id)
        s = Site.objects.filter(is_active=True).only("id").order_by("code").first()
        if s is not None:
            return int(s.id)
    except Exception:
        pass
    return 1


class Site(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    primary_domain = models.CharField(max_length=255, blank=True, default="")

    default_language_code = models.CharField(max_length=8, blank=True, default="")
    default_country_code = models.CharField(max_length=2, blank=True, default="")

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code
