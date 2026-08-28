import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import NamedTuple

from django.conf import settings
from glide import GlideClient, GlideClientConfiguration, NodeAddress


class _LoopClient(NamedTuple):
    """A client, and the generator that closes it when its event loop goes away."""

    client: GlideClient
    owner: AsyncGenerator[GlideClient]


_clients: dict[asyncio.AbstractEventLoop, _LoopClient] = {}
_lock = asyncio.Lock()


async def _managed_client(loop: asyncio.AbstractEventLoop) -> AsyncIterator[GlideClient]:
    """Create a client and close it again when the event loop that created it goes away.

    An event loop closes its async generators before it closes itself, which is where we get to
    close the client.
    """
    config = GlideClientConfiguration(
        [NodeAddress(settings.VALKEY_HOST, settings.VALKEY_PORT)],
        database_id=settings.VALKEY_DB,
    )
    client = await GlideClient.create(config)
    try:
        yield client
    finally:
        _clients.pop(loop, None)
        await client.close()


async def get_client() -> GlideClient:
    """Return this event loop's client, creating it on first use.

    One client per loop, because a client can only be used from the loop that created it: glide
    captures that loop in create() and routes every response back through it. From another loop a
    command doesn't fail, it stalls until something else happens to wake the caller, and once that
    loop is closed the response is dropped and the await never returns.

    The app runs on a single event loop and so has a single client. The other loops belong to
    whoever owns the process instead: manage.py commands, the django shell, and sync code
    publishing through async_to_sync.
    """
    current_loop = asyncio.get_running_loop()
    async with _lock:
        if current_loop not in _clients:
            await _close_clients_of_gone_loops()
            owner = _managed_client(current_loop)
            _clients[current_loop] = _LoopClient(await anext(owner), owner)
    return _clients[current_loop].client


async def _close_clients_of_gone_loops() -> None:
    """Clean up after a loop that was closed without finalizing its async generators."""
    # A copy, because closing a client removes its entry:
    for loop, entry in list(_clients.items()):
        if loop.is_closed():
            await entry.owner.aclose()


async def close_client() -> None:
    """Close the current event loops client, it's used from asgi.py."""
    current_loop = asyncio.get_running_loop()
    async with _lock:
        entry = _clients.get(current_loop)
        if entry is not None:
            await entry.owner.aclose()
