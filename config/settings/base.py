"""
Base settings shared by every environment.

Everything that differs between machines (WHMCS host, credentials, Redis,
throttle rates, TTLs) is read from the environment - never hardcoded.
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-do-not-use-in-prod")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "django_celery_results",
]

LOCAL_APPS = [
    "apps.core",
    "apps.whmcs",
    "apps.accounts",
    "apps.billing",
    "apps.support",
    "apps.hosting",
    "apps.orders",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "apps.core.middleware.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AccessLogMiddleware",
]

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

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"].setdefault("CONN_MAX_AGE", 60)

# ---------------------------------------------------------------------------
# i18n / static
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Cache - Redis when REDIS_URL is provided, in-memory otherwise.
# The rest of the codebase only ever touches django.core.cache, so switching
# backends is a pure env change.
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "bridge",
            "TIMEOUT": 300,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "bridge-locmem",
        }
    }

CACHE_TTL = {
    "catalogue": env.int("CACHE_TTL_CATALOGUE", default=900),
    "client": env.int("CACHE_TTL_CLIENT", default=60),
    "invoices": env.int("CACHE_TTL_INVOICES", default=45),
    "tickets": env.int("CACHE_TTL_TICKETS", default=30),
}

# ---------------------------------------------------------------------------
# Celery - eager (synchronous) until a broker is configured.
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="django-db")
CELERY_TASK_ALWAYS_EAGER = not bool(CELERY_BROKER_URL)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_TIME_LIMIT = 120
CELERY_TASK_SOFT_TIME_LIMIT = 90
CELERY_WORKER_PREFETCH_MULTIPLIER = 4
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "whmcs.provision.*": {"queue": "provisioning"},
    "whmcs.sync.*": {"queue": "sync"},
}

# ---------------------------------------------------------------------------
# WHMCS connection - the single place the upstream box is described.
# ---------------------------------------------------------------------------
WHMCS = {
    "SCHEME": env("WHMCS_SCHEME", default="https"),
    "HOST": env("WHMCS_HOST", default=""),
    "PORT": env.int("WHMCS_PORT", default=0) or None,
    "BASE_PATH": env("WHMCS_BASE_PATH", default="").rstrip("/"),
    "API_PATH": env("WHMCS_API_PATH", default="/includes/api.php"),
    "HOST_HEADER": env("WHMCS_HOST_HEADER", default=""),
    "IDENTIFIER": env("WHMCS_IDENTIFIER", default=""),
    "SECRET": env("WHMCS_SECRET", default=""),
    "ACCESS_KEY": env("WHMCS_ACCESS_KEY", default=""),
    "ADMIN_USERNAME": env("WHMCS_ADMIN_USERNAME", default=""),
    "ADMIN_PASSWORD": env("WHMCS_ADMIN_PASSWORD", default=""),
    "TIMEOUT": env.float("WHMCS_TIMEOUT", default=15.0),
    "CONNECT_TIMEOUT": env.float("WHMCS_CONNECT_TIMEOUT", default=5.0),
    "MAX_RETRIES": env.int("WHMCS_MAX_RETRIES", default=2),
    "RETRY_BACKOFF": env.float("WHMCS_RETRY_BACKOFF", default=0.4),
    "VERIFY_SSL": env.bool("WHMCS_VERIFY_SSL", default=True),
    "CA_BUNDLE": env("WHMCS_CA_BUNDLE", default=""),
    "MAX_CONNECTIONS": env.int("WHMCS_MAX_CONNECTIONS", default=20),
    "LOG_PAYLOADS": env.bool("LOG_WHMCS_PAYLOADS", default=False),
}

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.core.throttling.BurstAnonThrottle",
        "apps.core.throttling.SustainedUserThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="30/min"),
        "user": env("THROTTLE_USER", default="120/min"),
        # Per (IP, account) - one account cannot be hammered.
        "login": env("THROTTLE_LOGIN", default="8/min"),
        # Per IP across all accounts - stops credential stuffing.
        "login_ip": env("THROTTLE_LOGIN_IP", default="20/min"),
        "write": env("THROTTLE_WRITE", default="20/min"),
    },
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0) or None,
}

# Serve /api/docs/ and /api/schema/ outside DEBUG only when explicitly enabled.
EXPOSE_API_DOCS = env.bool("EXPOSE_API_DOCS", default=False)

SPECTACULAR_SETTINGS = {
    "TITLE": "WHMCS Bridge API",
    "DESCRIPTION": "Interface layer between the storefront SPA and WHMCS.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_TTL_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_TTL_DAYS", default=14)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.serializers.WhmcsTokenObtainPairSerializer",
}

# ---------------------------------------------------------------------------
# CORS / CSRF - the SPA is the only thing allowed to talk to us.
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:3000"])
CORS_ALLOW_CREDENTIALS = False
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=CORS_ALLOWED_ORIGINS)

# ---------------------------------------------------------------------------
# Logging - JSON-ish single line records carrying the request id.
# ---------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "apps.core.logging.RequestIDFilter"},
    },
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "httpx": {"level": "WARNING"},
    },
}
