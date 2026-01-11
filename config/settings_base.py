from __future__ import annotations

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    ALLOW_GUEST_CHECKOUT=(bool, True),
    JWT_ALGORITHM=(str, "HS256"),
    JWT_ACCESS_TTL_MINUTES=(int, 15),
    JWT_REFRESH_TTL_DAYS=(int, 30),
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

    ZB_PRODUCTS_FEED_URL=(str, ""),
    ZB_STOCKS_FEED_URL=(str, ""),

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
)

# Loads variables from .env if present (dev convenience). In prod use real env vars.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="unsafe-dev-secret-key")
DEBUG = env.bool("DEBUG", default=False)

NINJA_ENABLE_DOCS = env.bool("NINJA_ENABLE_DOCS", default=DEBUG)
NINJA_DOCS_REQUIRE_STAFF = env.bool("NINJA_DOCS_REQUIRE_STAFF", default=False)

ALLOW_GUEST_CHECKOUT = env.bool("ALLOW_GUEST_CHECKOUT", default=True)

JWT_ALGORITHM = env("JWT_ALGORITHM")
JWT_ACCESS_TTL_MINUTES = env.int("JWT_ACCESS_TTL_MINUTES")
JWT_REFRESH_TTL_DAYS = env.int("JWT_REFRESH_TTL_DAYS")

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
    "api",
    "accounts",
    "notifications",
    "catalog.apps.CatalogConfig",
    "cms.apps.CmsConfig",
    "homebuilder.apps.HomebuilderConfig",
    "zaliuojibanga",
    "shipping",
    "payments",
    "checkout",
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

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
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

LANGUAGE_CODE = env("LANGUAGE_CODE", default="en")
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

ZB_PRODUCTS_FEED_URL = env("ZB_PRODUCTS_FEED_URL", default="")
ZB_STOCKS_FEED_URL = env("ZB_STOCKS_FEED_URL", default="")

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
    AWS_S3_ADDRESSING_STYLE = env("AWS_S3_ADDRESSING_STYLE", default="auto")

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
            },
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
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

# --- Promotions/Coupons policy ---
COUPON_ALLOWED_CHANNELS = [c.strip().lower() for c in env.list("COUPON_ALLOWED_CHANNELS", default=["normal"]) if c.strip()]
