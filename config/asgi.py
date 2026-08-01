"""
ASGI entrypoint.

Kept ASGI-first from day one so adding Channels/WebSocket later is a matter of
wrapping ``application`` in a ProtocolTypeRouter - no re-platforming:

    from channels.routing import ProtocolTypeRouter, URLRouter
    application = ProtocolTypeRouter({
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    })
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

django_asgi_app = get_asgi_application()

application = django_asgi_app
