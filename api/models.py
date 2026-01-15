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


class SiteConfig(models.Model):
    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="config")

    default_from_email = models.CharField(max_length=255, blank=True, default="")
    smtp_host = models.CharField(max_length=255, blank=True, default="")
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_user = models.CharField(max_length=255, blank=True, default="")
    smtp_password = models.CharField(max_length=255, blank=True, default="")
    smtp_use_tls = models.BooleanField(default=True)
    smtp_use_ssl = models.BooleanField(default=False)
    smtp_timeout = models.PositiveIntegerField(default=10)

    terms_url = models.URLField(blank=True, default="")
    privacy_url = models.URLField(blank=True, default="")
    terms_version = models.CharField(max_length=50, blank=True, default="")
    privacy_version = models.CharField(max_length=50, blank=True, default="")

    neopay_project_id = models.BigIntegerField(null=True, blank=True)
    neopay_project_key = models.CharField(max_length=255, blank=True, default="")
    neopay_client_redirect_url = models.URLField(blank=True, default="")
    neopay_enable_bank_preselect = models.BooleanField(default=False)

    category_path_template = models.CharField(max_length=255, blank=True, default="/c/{slug}")
    brand_path_template = models.CharField(max_length=255, blank=True, default="/b/{slug}")
    cms_page_path_template = models.CharField(max_length=255, blank=True, default="/page/{slug}")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["site__code"]

    def __str__(self) -> str:
        return f"{self.site.code} config"
