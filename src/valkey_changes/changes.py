import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from glide import ClosingError, GlideClient

from valkey_changes import valkey_client

logger = logging.getLogger(__name__)

# How long to wait for valkey to acknowledge a subscribe or unsubscribe before giving up.
_SUBSCRIBE_TIMEOUT_MS = 5000


class Source:
    """One channel that changes() can wait on.

    A plain source is a wake-up and nothing more. Subclasses can carry a value instead, by taking
    the message payload apart in "_consume"; see StateStore.subscribe.

    Every source handed to one changes() call shares that call's event, so a single wait covers all
    of them. Each still keeps its own payload and its own record of having fired.
    """

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self._registry: _Registry | None = None
        self._event: asyncio.Event | None = None
        self._payload: bytes | None = None
        self._fired = False

    def _deliver(self, payload: bytes | None) -> None:
        """Hand an incoming message over, called by the pump.

        "payload" is None when the wake carries no message, which is how _consume tells a real
        change apart from the pump waking everyone after a failure.
        """
        if self._event is None:
            # Delivered after _stop(), there is nobody left to wake.
            return
        self._payload = payload
        self._fired = True
        self._event.set()

    def _take(self) -> tuple[bool, bytes | None]:
        """Whether a message arrived since the last call, and its body, both consumed."""
        fired = self._fired
        payload = self._payload
        self._fired = False
        self._payload = None
        return fired, payload

    async def _start(self, event: asyncio.Event) -> None:
        """Begin listening, sharing the event that changes() waits on."""
        # Before add(), because a message can arrive while the subscribe below is still in flight:
        self._event = event
        registry = await _get_registry()
        await registry.add(self)
        self._registry = registry

    async def _stop(self) -> None:
        """Stop listening, releasing the channel if no other source still wants it.

        Safe to call more than once, which matters because the SSE generators and the requests that
        own a source get cancelled at awkward moments.
        """
        registry = self._registry
        if registry is None:
            return
        self._registry = None
        self._event = None
        # Nobody is left to consume what arrived just before this, so don't keep it around for a
        # later _start() to trip over.
        self._payload = None
        self._fired = False
        await registry.remove(self)

    async def _consume(self) -> bool:
        """Take whatever arrived since the last call, and say whether anything did."""
        fired, _ = self._take()
        return fired


class _Registry:
    """The sources of one event loop's client, and the pump task that feeds them.

    One registry per loop, because a glide client only works with the loop that created it and so
    does everything waiting on it.
    """

    def __init__(self, client: GlideClient) -> None:
        self.client = client
        self._by_channel: dict[str, set[Source]] = {}
        self._pump: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def add(self, source: Source) -> None:
        """Register a source, subscribing to its channel if nobody wanted it yet."""
        async with self._lock:
            # Before the round trip below, so that nothing can arrive while nobody is draining:
            self._start_pump()
            sources = self._by_channel.setdefault(source.channel, set())
            is_new_channel = not sources
            sources.add(source)
            if is_new_channel:
                try:
                    await self.client.subscribe({source.channel}, timeout_ms=_SUBSCRIBE_TIMEOUT_MS)
                except BaseException:
                    # Don't leave a channel behind that we never actually subscribed to.
                    self._forget(source)
                    if not self._by_channel:
                        self._stop_pump()
                    raise

    async def remove(self, source: Source) -> None:
        """Unregister a source, unsubscribing from its channel if nobody wants it any more."""
        async with self._lock:
            if self._forget(source):
                try:
                    await self.client.unsubscribe(
                        {source.channel}, timeout_ms=_SUBSCRIBE_TIMEOUT_MS
                    )
                except ClosingError:
                    # The client is already closed, which took its subscriptions with it.
                    pass
            if not self._by_channel:
                self._stop_pump()

    def _forget(self, source: Source) -> bool:
        """Remove a source and say whether its channel is now unwanted."""
        sources = self._by_channel.get(source.channel)
        if sources is None:
            # Already removed, this is a second _stop().
            return False
        sources.discard(source)
        if sources:
            return False
        del self._by_channel[source.channel]
        return True

    def _start_pump(self) -> None:
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._pump_messages())

    def _stop_pump(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            self._pump = None

    async def _pump_messages(self) -> None:
        """Fan every incoming message out to the sources listening on its channel.

        Only one consumer may drain a glide client's pubsub queue, so this task is the single
        reader and everyone else waits on their own event.
        """
        try:
            while True:
                message = await self.client.get_pubsub_message()
                channel = message.channel
                if isinstance(channel, bytes):
                    channel = channel.decode()
                for source in self._by_channel.get(channel, ()):
                    source._deliver(message.message)
        except asyncio.CancelledError:
            raise
        except ClosingError:
            logger.debug("The valkey client closed, the subscription pump is stopping.")
        except Exception:
            # Nobody awaits this task, so this is the only place the failure gets reported.
            logger.exception("The valkey subscription pump stopped, subscribers will stop waking.")
        finally:
            self._wake_everyone()

    def _wake_everyone(self) -> None:
        """Wake every source, for when we can no longer tell them what changed.

        A source that wakes for nothing re-reads the database and finds it unchanged, which costs
        one round trip. One that never wakes hangs until its own timeout, if it has one.
        """
        # Deliberately no payload: a source that takes it gets None and falls back to a fresh read,
        # which is exactly right when we don't know what it missed.
        for sources in self._by_channel.values():
            for source in sources:
                source._deliver(None)

    async def close(self) -> None:
        async with self._lock:
            pump = self._pump
            self._pump = None
            self._by_channel.clear()
        if pump is not None:
            pump.cancel()
            # return_exceptions so that a pump which already failed doesn't take shutdown with it.
            await asyncio.gather(pump, return_exceptions=True)


_registries: dict[asyncio.AbstractEventLoop, _Registry] = {}


async def _get_registry() -> _Registry:
    client = await valkey_client.get_client()
    loop = asyncio.get_running_loop()
    for closed_loop in [entry for entry in _registries if entry.is_closed()]:
        del _registries[closed_loop]
    registry = _registries.get(loop)
    # A registry is only good for the client it was built for. Rebuilding when the client changed
    # covers a close_client() followed by more work on the same loop.
    if registry is None or registry.client is not client:
        registry = _Registry(client)
        _registries[loop] = registry
    return registry


class Changes:
    """A single point to wait on, however many sources are behind it."""

    def __init__(self, event: asyncio.Event, sources: tuple[Source, ...]) -> None:
        self._event = event
        self._sources = sources

    async def wait(self) -> tuple[Source, ...]:
        """Wait until at least one source fires, and return the ones that did.

        The event is cleared before this returns, so anything published while you act on the result
        wakes the next wait instead of being swallowed. Ignore the return value when all you care
        about is that something changed.
        """
        await self._event.wait()
        self._event.clear()
        fired = []
        for source in self._sources:
            if await source._consume():
                fired.append(source)
        return tuple(fired)


@asynccontextmanager
async def changes(*sources: Source) -> AsyncIterator[Changes]:
    """Listen to every source at once, and unsubscribe them all again on the way out.

    One event covers all of them, so a caller waits once no matter how many sources there are:

        tab = _store.subscribe(tab_id, user_id)
        async with changes(tab, podcast_publisher.subscribe()) as changed:
            while True:
                ...render(tab.state)...
                await changed.wait()
    """
    if not sources:
        raise ValueError("changes() needs at least one source.")
    event = asyncio.Event()
    started: list[Source] = []
    try:
        for source in sources:
            await source._start(event)
            started.append(source)
        yield Changes(event, sources)
    finally:
        for source in reversed(started):
            await source._stop()


async def shutdown() -> None:
    """Stop this loop's pump and forget its sources, it's used from asgi.py."""
    registry = _registries.pop(asyncio.get_running_loop(), None)
    if registry is not None:
        await registry.close()
