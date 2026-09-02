import asyncio
import secrets
from collections.abc import Callable
from typing import Generic, TypeVar

import msgspec
from django.core.exceptions import PermissionDenied
from glide import Batch, ExpirySet, ExpiryType

from valkey_changes import changes, valkey_client

T = TypeVar("T", bound=msgspec.Struct)


class StateStore(Generic[T]):
    def __init__(
        self,
        struct_type: type[T],
        namespace: str,
        default_factory: Callable[[str], T],
    ) -> None:
        self._type = struct_type
        self._namespace = namespace
        self._default_factory = default_factory

    def channel(self, tab_id: str) -> str:
        return f"{self._namespace}:state_updates:{tab_id}"

    def _key(self, tab_id: str) -> str:
        return f"{self._namespace}:state:{tab_id}"

    def _decode(self, data: bytes) -> T:
        return msgspec.msgpack.decode(data, type=self._type)

    def _check_owner(self, tab_id: str, user_id: int | None) -> None:
        """Ensure the state belongs to the user_id"""
        owner, _, _ = tab_id.partition(":")
        if owner != str(user_id or 0):
            raise PermissionDenied("This tab does not belong to the current user.")

    async def new(self, user_id: int | None) -> T:
        """Create a new state for the given `user_id`.

        In order to support anonymous users, `user_id` can be None. Note the
        lessened privacy in that case, tho the secret part of `tab_id` should
        be very unguessable anyway, so this is not a big deal.
        """
        secret_part = secrets.token_urlsafe(16)
        tab_id = f"{user_id or 0}:{secret_part}"
        state = self._default_factory(tab_id)
        await self.save(tab_id, state, user_id)
        return state

    async def _get(self, tab_id: str) -> T:
        vk = await valkey_client.get_client()
        data = await vk.get(self._key(tab_id))
        if data is None:
            return self._default_factory(tab_id)
        return self._decode(data)

    async def get(self, tab_id: str, user_id: int | None) -> T:
        self._check_owner(tab_id, user_id)
        return await self._get(tab_id)

    async def save(
        self, tab_id: str, state: T, user_id: int | None, expire_seconds: int = 60 * 60 * 24
    ) -> None:
        """Atomically save and publish the value."""
        self._check_owner(tab_id, user_id)
        packed = msgspec.msgpack.encode(state)
        batch = Batch(is_atomic=True)
        batch.set(
            self._key(tab_id),
            packed,
            expiry=ExpirySet(ExpiryType.SEC, expire_seconds),
        )
        batch.publish(packed, self.channel(tab_id))
        vk = await valkey_client.get_client()
        await vk.exec(batch, raise_on_error=True)

    def subscribe(self, tab_id: str, user_id: int | None) -> StateSource[T]:
        self._check_owner(tab_id, user_id)
        return StateSource(self, tab_id)


class StateSource(changes.Source, Generic[T]):
    """A tab's stored state, kept current for as long as changes() holds it."""

    def __init__(self, store: StateStore[T], tab_id: str) -> None:
        super().__init__(store.channel(tab_id))
        self._store = store
        self._tab_id = tab_id
        self._state: T | None = None

    @property
    def state(self) -> T:
        """The state as of the last Changes.wait().

        Only readable inside changes(), and only moved on by wait() - reading this twice without a
        wait in between gives the same answer even if a save landed. That suits the loop it is
        written for, which renders, waits, and renders again.
        """
        if self._state is None:
            raise RuntimeError("This state is only readable inside changes().")
        return self._state

    async def _start(self, event: asyncio.Event) -> None:
        await super()._start(event)
        self._state = await self._store._get(self._tab_id)

    async def _consume(self) -> bool:
        fired, payload = self._take()
        if not fired:
            return False
        if payload is None:
            # A wake with nothing attached, so we don't know what we missed. Read it back.
            self._state = await self._store._get(self._tab_id)
        else:
            self._state = self._store._decode(payload)
        return True
