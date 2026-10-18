from __future__ import annotations

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    JWT_ALGORITHM=(str, "HS256"),
    JWT_ACCESS_TTL_MINUTES=(int, 15),
    JWT_REFRESH_TTL_DAYS=(int, 30),

    AUTH_COOKIE_ACCESS_NAME=(str, "access_token"),
    AUTH_COOKIE_REFRESH_NAME=(str, "refresh_token"),
    AUTH_COOKIE_SAMESITE=(str, "lax"),
    AUTH_COOKIE_SECURE=(str, ""),
    AUTH_COOKIE_DOMAIN=(str, ""),
    RECENTLY_VIEWED_MAX=(int, 12),
    EMAIL_OTP_CODE_LENGTH=(int, 6),
    EMAIL_OTP_TTL_MINUTES=(int, 10),
    EMAIL_OTP_RESEND_COOLDOWN_SECONDS=(int, 30),
    EMAIL_OTP_MAX_ATTEMPTS=(int, 5),

    EMAIL_BACKEND=(str, "django.core.mail.backends.console.EmailBackend"),
    EMAIL_HOST=(str, ""),
    EMAIL_PORT=(int, 587),
    EMAIL_USE_TLS=(bool, True),
    EMAIL_HOST_USER=(str, ""),
    EMAIL_HOST_PASSWORD=(str, ""),
    DEFAULT_FROM_EMAIL=(str, ""),

    MEDIA_STORAGE=(str, "local"),  # local | s3
    DJANGO_USE_S3=(bool, False),
    AWS_ACCESS_KEY_ID=(str, ""),
    AWS_SECRET_ACCESS_KEY=(str, ""),
    AWS_STORAGE_BUCKET_NAME=(str, ""),
    AWS_S3_ENDPOINT_URL=(str, ""),
    AWS_S3_REGION_NAME=(str, ""),
    AWS_S3_CUSTOM_DOMAIN=(str, ""),
    AWS_DEFAULT_ACL=(str, ""),
    AWS_QUERYSTRING_AUTH=(bool, False),
    AWS_S3_ADDRESSING_STYLE=(str, "auto"),

    THUMB_SIZE=(int, 150),
    MEDIUM_SIZE=(int, 300),
    LARGE_SIZE=(int, 600),

    # Listing image normalization (square thumbnails)
    LISTING_IMAGE_SIZE=(int, 300),
    LISTING_TRIM_TOLERANCE=(int, 18),

    # Shipping (MVP)
    LPEXPRESS_SHIPPING_NET_EUR=(str, "0.00"),
    DEFAULT_SHIPPING_TAX_CLASS_CODE=(str, "standard"),

    # Checkout order-level consents (front-end needs current versions)
    CHECKOUT_TERMS_VERSION=(str, ""),
    CHECKOUT_PRIVACY_VERSION=(str, ""),
    CHECKOUT_TERMS_URL=(str, ""),
    CHECKOUT_PRIVACY_URL=(str, ""),

    # DPD (carrier)
    DPD_BASE_URL=(str, "https://esiunta.dpd.lt/api/v1"),
    DPD_TOKEN=(str, ""),
    # Backward compatible alias (same value as DPD_TOKEN)
    DPD_API_KEY=(str, ""),
    DPD_STATUS_LANG=(str, "lt"),

    # DPD shipments/labels
    DPD_SENDER_NAME=(str, ""),
    DPD_SENDER_PHONE=(str, ""),
    DPD_SENDER_STREET=(str, ""),
    DPD_SENDER_CITY=(str, ""),
    DPD_SENDER_POSTAL_CODE=(str, ""),
    DPD_SENDER_COUNTRY=(str, ""),
    DPD_PAYER_CODE=(str, ""),
    DPD_SERVICE_ALIAS_LOCKER=(str, ""),
    DPD_SERVICE_ALIAS_COURIER=(str, ""),

    # Promotions/Coupons policy
    COUPON_ALLOWED_CHANNELS=(list, ["normal"]),
    SHOP_CONTEXT_ENABLED=(bool, True),
    SHOP_CONTEXT_STRICT=(bool, False),

    # Redis / Celery
    REDIS_URL=(str, "redis://localhost:6379/0"),
    CELERY_BROKER_URL=(str, ""),
    CELERY_RESULT_BACKEND=(str, "django-db"),
)

# Loads variables from .env if present (dev convenience). In prod use real env vars.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="unsafe-dev-secret-key")
DEBUG = env.bool("DEBUG", default=False)

NINJA_ENABLE_DOCS = env.bool("NINJA_ENABLE_DOCS", default=DEBUG)
NINJA_DOCS_REQUIRE_STAFF = env.bool("NINJA_DOCS_REQUIRE_STAFF", default=False)

JWT_ALGORITHM = env("JWT_ALGORITHM")
JWT_ACCESS_TTL_MINUTES = env.int("JWT_ACCESS_TTL_MINUTES")
JWT_REFRESH_TTL_DAYS = env.int("JWT_REFRESH_TTL_DAYS")

AUTH_COOKIE_ACCESS_NAME = env("AUTH_COOKIE_ACCESS_NAME")
AUTH_COOKIE_REFRESH_NAME = env("AUTH_COOKIE_REFRESH_NAME")
AUTH_COOKIE_SAMESITE = env("AUTH_COOKIE_SAMESITE", default="lax").lower()
AUTH_COOKIE_SECURE_RAW = (env("AUTH_COOKIE_SECURE", default="") or "").strip().lower()
AUTH_COOKIE_SECURE = None
if AUTH_COOKIE_SECURE_RAW in {"true", "1", "yes"}:
    AUTH_COOKIE_SECURE = True
elif AUTH_COOKIE_SECURE_RAW in {"false", "0", "no"}:
    AUTH_COOKIE_SECURE = False
AUTH_COOKIE_DOMAIN = (env("AUTH_COOKIE_DOMAIN", default="") or "").strip() or None

AUTH_RETURN_ACCESS_IN_BODY = env.bool("AUTH_RETURN_ACCESS_IN_BODY", default=False)

RECENTLY_VIEWED_MAX = env.int("RECENTLY_VIEWED_MAX", default=12)

EMAIL_OTP_CODE_LENGTH = env.int("EMAIL_OTP_CODE_LENGTH")
EMAIL_OTP_TTL_MINUTES = env.int("EMAIL_OTP_TTL_MINUTES")
EMAIL_OTP_RESEND_COOLDOWN_SECONDS = env.int(
    "EMAIL_OTP_RESEND_COOLDOWN_SECONDS")
EMAIL_OTP_MAX_ATTEMPTS = env.int("EMAIL_OTP_MAX_ATTEMPTS")

EMAIL_BACKEND = env("EMAIL_BACKEND", default=env(
    "DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"))
EMAIL_HOST = env("EMAIL_HOST", default=env("DJANGO_EMAIL_HOST", default=""))
EMAIL_PORT = env.int("EMAIL_PORT", default=env.int(
    "DJANGO_EMAIL_PORT", default=587))
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=env.bool(
    "DJANGO_EMAIL_USE_TLS", default=True))
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=env.bool(
    "DJANGO_EMAIL_USE_SSL", default=False))
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=env.int(
    "DJANGO_EMAIL_TIMEOUT", default=10))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default=env(
    "DJANGO_EMAIL_HOST_USER", default=""))
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default=env(
    "DJANGO_EMAIL_HOST_PASSWORD", default=""))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=env(
    "DJANGO_DEFAULT_FROM_EMAIL", default=""))

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "mptt",
    "django_celery_results",
    "django_celery_beat",
    "api",
    "accounts",
    "notifications",
    "analytics.apps.AnalyticsConfig",
    "catalog.apps.CatalogConfig",
    "catalog_import",
    "cms.apps.CmsConfig",
    "homebuilder.apps.HomebuilderConfig",
    "shipping",
    "payments",
    "checkout",
    "billing.apps.BillingConfig",
    "promotions",
    "dpd",
    "unisend",
]

DPD_BASE_URL = env("DPD_BASE_URL", default="https://esiunta.dpd.lt/api/v1")
# Prefer DPD_TOKEN, but allow legacy DPD_API_KEY
DPD_TOKEN = env("DPD_TOKEN", default=env("DPD_API_KEY", default=""))
DPD_STATUS_LANG = env("DPD_STATUS_LANG", default="lt")

DPD_SENDER_NAME = env("DPD_SENDER_NAME", default="")
DPD_SENDER_PHONE = env("DPD_SENDER_PHONE", default="")
DPD_SENDER_STREET = env("DPD_SENDER_STREET", default="")
DPD_SENDER_CITY = env("DPD_SENDER_CITY", default="")
DPD_SENDER_POSTAL_CODE = env("DPD_SENDER_POSTAL_CODE", default="")
DPD_SENDER_COUNTRY = env("DPD_SENDER_COUNTRY", default="")
DPD_PAYER_CODE = env("DPD_PAYER_CODE", default="")
DPD_SERVICE_ALIAS_LOCKER = env("DPD_SERVICE_ALIAS_LOCKER", default="")
DPD_SERVICE_ALIAS_COURIER = env("DPD_SERVICE_ALIAS_COURIER", default="")

# Montonio (Stargate API) — online payments
MONTONIO_API_URL = env("MONTONIO_API_URL", default="")
MONTONIO_ACCESS_KEY = env("MONTONIO_ACCESS_KEY", default="")
MONTONIO_SECRET_KEY = env("MONTONIO_SECRET_KEY", default="")
MONTONIO_RETURN_URL = env("MONTONIO_RETURN_URL", default="")
MONTONIO_NOTIFICATION_URL = env("MONTONIO_NOTIFICATION_URL", default="")
MONTONIO_LOCALE = env("MONTONIO_LOCALE", default="lt")
INBANK_API_URL = env("INBANK_API_URL", default="")
INBANK_API_KEY = env("INBANK_API_KEY", default="")
INBANK_SHOP_UUID = env("INBANK_SHOP_UUID", default="")
INBANK_PRODUCT_CODE = env("INBANK_PRODUCT_CODE", default="")
INBANK_LOCALE = env("INBANK_LOCALE", default="lt-LT")
INBANK_MERCHANT_DOMAIN = env("INBANK_MERCHANT_DOMAIN", default="")
INBANK_RETURN_URL = env("INBANK_RETURN_URL", default="")
INBANK_CANCEL_URL = env("INBANK_CANCEL_URL", default="")
INBANK_CALLBACK_URL = env("INBANK_CALLBACK_URL", default="")

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "api.middleware.ShopResolverMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5432/django_ecommerce",
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

_DEFAULT_LANGUAGE_NAMES = {
    "lt": "Lithuanian",
    "pl": "Polish",
    "en": "English",
    "de": "German",
    "lv": "Latvian",
    "et": "Estonian",
}
_configured_supported_languages = [
    (c or "").split("-")[0].strip().lower()
    for c in env.list("SUPPORTED_LANGUAGE_CODES", default=["lt", "pl", "en"])
    if (c or "").strip()
]
_normalized_supported_languages: list[str] = []
for _code in _configured_supported_languages:
    if _code not in _normalized_supported_languages:
        _normalized_supported_languages.append(_code)
if not _normalized_supported_languages:
    _normalized_supported_languages = ["lt", "pl", "en"]

LANGUAGE_CODE = (env("LANGUAGE_CODE", default=_normalized_supported_languages[0]) or _normalized_supported_languages[0]).split("-")[0].strip().lower()
if LANGUAGE_CODE not in _normalized_supported_languages:
    _normalized_supported_languages.insert(0, LANGUAGE_CODE)

LANGUAGES = [(_code, _DEFAULT_LANGUAGE_NAMES.get(_code, _code.upper())) for _code in _normalized_supported_languages]
SUPPORTED_LANGUAGE_CODES = [c for c, _name in LANGUAGES]
LANGUAGE_QUERY_PARAM = env("LANGUAGE_QUERY_PARAM", default="lang")
TIME_ZONE = env("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Media uploads (product images, etc.)
MEDIA_URL = env("MEDIA_URL", default="/media/")
MEDIA_ROOT = BASE_DIR / "media"

THUMB_SIZE = env.int("THUMB_SIZE", default=150)
MEDIUM_SIZE = env.int("MEDIUM_SIZE", default=300)
LARGE_SIZE = env.int("LARGE_SIZE", default=600)

LISTING_IMAGE_SIZE = env.int("LISTING_IMAGE_SIZE", default=MEDIUM_SIZE)
LISTING_TRIM_TOLERANCE = env.int("LISTING_TRIM_TOLERANCE", default=18)

LPEXPRESS_SHIPPING_NET_EUR = env("LPEXPRESS_SHIPPING_NET_EUR", default="0.00")
DEFAULT_SHIPPING_TAX_CLASS_CODE = env(
    "DEFAULT_SHIPPING_TAX_CLASS_CODE", default="standard")

# Checkout consents (defaults are safe for dev; set explicit versions in prod)
CHECKOUT_TERMS_VERSION = env("CHECKOUT_TERMS_VERSION", default="v1")
CHECKOUT_PRIVACY_VERSION = env("CHECKOUT_PRIVACY_VERSION", default="v1")
CHECKOUT_TERMS_URL = env("CHECKOUT_TERMS_URL", default="/terms")
CHECKOUT_PRIVACY_URL = env("CHECKOUT_PRIVACY_URL", default="/privacy")

# Backward-compatible toggle: older setups use DJANGO_USE_S3=True
MEDIA_STORAGE = env("MEDIA_STORAGE", default="local").lower()
if env.bool("DJANGO_USE_S3", default=False):
    MEDIA_STORAGE = "s3"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if MEDIA_STORAGE == "s3":
    INSTALLED_APPS += ["storages"]

    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL")
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="")
    AWS_S3_CUSTOM_DOMAIN = env("AWS_S3_CUSTOM_DOMAIN", default="")
    AWS_DEFAULT_ACL = env("AWS_DEFAULT_ACL", default=None)
    AWS_QUERYSTRING_AUTH = env.bool("AWS_QUERYSTRING_AUTH", default=False)
    AWS_S3_ADDRESSING_STYLE = env("AWS_S3_ADDRESSING_STYLE", default="path")

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL,
                "region_name": AWS_S3_REGION_NAME or None,
                "custom_domain": AWS_S3_CUSTOM_DOMAIN or None,
                "default_acl": AWS_DEFAULT_ACL,
                "querystring_auth": AWS_QUERYSTRING_AUTH,
                "addressing_style": AWS_S3_ADDRESSING_STYLE,
                "file_overwrite": False,
            },
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

    # Private documents (invoices, shipping labels): separate bucket with NO
    # public access/custom domain. Signed URLs only when needed; app streams.
    AWS_PRIVATE_BUCKET_NAME = env("AWS_PRIVATE_BUCKET_NAME", default="")
    if AWS_PRIVATE_BUCKET_NAME:
        STORAGES["private"] = {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            "OPTIONS": {
                "bucket_name": AWS_PRIVATE_BUCKET_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL,
                "region_name": AWS_S3_REGION_NAME or None,
                "custom_domain": None,
                "default_acl": "private",
                "querystring_auth": True,
                "addressing_style": AWS_S3_ADDRESSING_STYLE,
                "file_overwrite": False,
            },
        }

# --- API/front-end integration ---
# Default dev origins for SvelteKit
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:5173", "http://127.0.0.1:5173"],
)
CORS_ALLOW_CREDENTIALS = env.bool("CORS_ALLOW_CREDENTIALS", default=True)

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:5173", "http://127.0.0.1:5173"],
)

# API routing
API_BASE_PATH = env("API_BASE_PATH", default=env(
    "NINJA_BASE_PATH", default="api"))

# Ops katalogo importai gali būti kelių MB JSON dokumentai.
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int(
    "DATA_UPLOAD_MAX_MEMORY_SIZE", default=100 * 1024 * 1024)

# --- Promotions/Coupons policy ---
COUPON_ALLOWED_CHANNELS = [c.strip().lower() for c in env.list("COUPON_ALLOWED_CHANNELS", default=["normal"]) if c.strip()]
SHOP_CONTEXT_ENABLED = env.bool("SHOP_CONTEXT_ENABLED", default=True)
SHOP_CONTEXT_STRICT = env.bool("SHOP_CONTEXT_STRICT", default=False)

# --- Redis / Celery ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL) or REDIS_URL
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="django-db")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
