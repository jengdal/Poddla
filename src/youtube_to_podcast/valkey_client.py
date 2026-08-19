import asyncio
from collections.abc import Callable
from typing import Any

from django.conf import settings
from glide import GlideClient, GlideClientConfiguration, NodeAddress

_client: GlideClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None
_lock = asyncio.Lock()


async def get_client() -> GlideClient:
    global _client, _client_loop
    current_loop = asyncio.get_running_loop()
    async with _lock:
        # Ensure we only use the _client object from the same event loop that created it.
        # This is mainly a thing for when using the django shell.
        if _client is None or _client_loop is not current_loop:
            config = GlideClientConfiguration(
                [NodeAddress(settings.VALKEY_HOST, settings.VALKEY_PORT)]
            )
            _client = await GlideClient.create(config)
            _client_loop = current_loop
    return _client


async def create_subscriber(
    channel: str,
    callback: Callable | None = None,
    context: Any = None,
) -> GlideClient:
    nodes = [NodeAddress(settings.VALKEY_HOST, settings.VALKEY_PORT)]
    if callback is not None:
        config = GlideClientConfiguration(
            nodes,
            pubsub_subscriptions=GlideClientConfiguration.PubSubSubscriptions(
                channels_and_patterns={
                    GlideClientConfiguration.PubSubChannelModes.Exact: {channel}
                },
                callback=callback,
                context=context,
            ),
        )
        return await GlideClient.create(config)
    config = GlideClientConfiguration(nodes)
    client = await GlideClient.create(config)
    await client.subscribe({channel}, timeout_ms=5000)
    return client


async def close_client() -> None:
    global _client
    async with _lock:
        if _client is not None:
            await _client.close()
            _client = None
