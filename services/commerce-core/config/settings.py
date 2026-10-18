"""commerce-core settings.

Every value comes from the environment (see the repository's .env.example).
Defaults are the safe localhost/Compose values, so nothing here is hard-coded
to a deployment.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-django-secret")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,commerce-core")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.postgres",
    "rest_framework",
    "common",
    "catalog",
    "orders",
    "outbox",
]

MIDDLEWARE = [
    # First, before anything parses the request: only the BFF gets in.
    "common.gateway.GatewayGuardMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# All commerce tables live in the `commerce` schema (created by the first
# migration in `common`). The catalog reader only ever sees a view in it.
DB_SCHEMA = env("COMMERCE_DB_SCHEMA", "commerce")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("POSTGRES_HOST", "postgres"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "NAME": env("POSTGRES_DB", "shop"),
        "USER": env("COMMERCE_DB_USER", "commerce"),
        "PASSWORD": env("COMMERCE_DB_PASSWORD", "dev-only-commerce"),
        "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "options": f"-c search_path={DB_SCHEMA},public -c statement_timeout={env('DB_STATEMENT_TIMEOUT_MS', '5000')}",
            "connect_timeout": int(env("DB_CONNECT_TIMEOUT_SECONDS", "5")),
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
USE_I18N = False

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["common.auth.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "common.errors.problem_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# --------------------------------------------------------------------------
# Gateway guard (docs/request-signing.md). Fail closed: no secret, no start.
# --------------------------------------------------------------------------
GATEWAY_SECRET = env("GATEWAY_SECRET", "")
GATEWAY_ALLOW_UNGUARDED = env_bool("GATEWAY_ALLOW_UNGUARDED", False)
GATEWAY_OPEN_PATHS = ("/healthz", "/readyz")  # container probes only; they reveal nothing

# --------------------------------------------------------------------------
# Auth: tokens are issued by auth-service and validated here, locally.
# --------------------------------------------------------------------------
# Production path: RS256 tokens verified against the auth-service JWKS.
JWT_JWKS_URL = env("INTERNAL_AUTH_JWKS_URL", "")
JWT_ISSUER = env("PUBLIC_AUTH_ISSUER_URL", "http://localhost:8080")  # the BFF origin (ADR 012)
# bff: user tokens the BFF forwards. core: SYSTEM and break-glass tokens (docs/break-glass.md).
JWT_AUDIENCE = env_list("JWT_AUDIENCE", "") or None
# Dev/test path: HS256 tokens signed with a shared secret. Only honoured when
# set; used until auth-service exists and by the test suite.
JWT_DEV_HS256_SECRET = env("JWT_DEV_HS256_SECRET", "")
JWT_LEEWAY_SECONDS = int(env("JWT_LEEWAY_SECONDS", "30"))

# --------------------------------------------------------------------------
# Commerce behaviour
# --------------------------------------------------------------------------
RESERVATION_TTL_SECONDS = int(env("RESERVATION_TTL_SECONDS", "900"))
IDEMPOTENCY_RETENTION_HOURS = int(env("IDEMPOTENCY_RETENTION_HOURS", "24"))
MAX_PRODUCT_IMAGES = 8
CATALOG_PAGE_SIZE = int(env("CATALOG_PAGE_SIZE", "24"))
DEFAULT_CURRENCY = env("DEFAULT_CURRENCY", "USD")
LOW_STOCK_THRESHOLD = int(env("LOW_STOCK_THRESHOLD", "5"))
# Payments (orders/payments.py). The provider's address is fixed: the private network's name.
PAYMENTS_URL = "http://payments-sim:8003"
PAYMENT_PROVIDER_TIMEOUT_SECONDS = float(env("PAYMENT_PROVIDER_TIMEOUT_SECONDS", "3"))
PAYMENT_RECONCILE_AFTER_SECONDS = int(env("PAYMENT_RECONCILE_AFTER_SECONDS", "15"))
COMMERCE_SERVICE_CLIENT_ID = env("COMMERCE_SERVICE_CLIENT_ID", "")
COMMERCE_SERVICE_CLIENT_SECRET = env("COMMERCE_SERVICE_CLIENT_SECRET", "")
PUBLIC_ASSET_BASE_URL = env("PUBLIC_ASSET_BASE_URL", "http://localhost:9000/product-images")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": '{"ts":"%(asctime)s","level":"%(levelname)s","service":"commerce-core","logger":"%(name)s","msg":"%(message)s"}'
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "info").upper()},
}
