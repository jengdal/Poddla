import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "poddla.settings")

_django_app = get_asgi_application()


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                from podcasts import downloader

                # NOTE: If you want to run multiple web worker processes, you should move the
                #       downloader to its own process, it will have problems if you run multiple
                #       instances of it.
                downloader.start()

                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                # Add any global cleanup calls here.
                from podcasts import downloader
                from valkey_changes import changes, valkey_client

                await downloader.stop()
                # Before close_client(), so that the pump stops on purpose rather than by
                # discovering its client has gone.
                await changes.shutdown()
                await valkey_client.close_client()
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        await _django_app(scope, receive, send)
