import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "poddla.settings")

_django_app = get_asgi_application()


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                # Add any global cleanup calls here.
                from valkey_changes import changes, valkey_client

                # Before close_client(), so that the pump stops on purpose rather than by
                # discovering its client has gone.
                await changes.shutdown()
                await valkey_client.close_client()
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        await _django_app(scope, receive, send)
