import asyncio

from django.conf import settings
from glide import GlideClient, GlideClientConfiguration, NodeAddress

_client: GlideClient | None = None
_lock = asyncio.Lock()


async def get_client() -> GlideClient:
    global _client
    async with _lock:
        if _client is None:
            config = GlideClientConfiguration(
                [NodeAddress(settings.VALKEY_HOST, settings.VALKEY_PORT)]
            )
            _client = await GlideClient.create(config)
    return _client


async def create_subscriber(channel: str) -> GlideClient:
    config = GlideClientConfiguration(
        [NodeAddress(settings.VALKEY_HOST, settings.VALKEY_PORT)]
    )
    client = await GlideClient.create(config)
    await client.subscribe({channel}, timeout_ms=5000)
    return client


async def close_client() -> None:
    global _client
    async with _lock:
        if _client is not None:
            await _client.close()
            _client = None
