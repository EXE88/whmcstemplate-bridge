"""Development settings."""

from .base import *  # noqa: F403
from .base import env

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS += ["django_extensions"]  # noqa: F405

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

# Loosen throttling locally unless explicitly set.
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"].update(  # noqa: F405
    {
        "anon": env("THROTTLE_ANON", default="200/min"),
        "user": env("THROTTLE_USER", default="600/min"),
    }
)

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
