from collections.abc import Callable
from typing import Generic, TypeVar

import msgspec
from glide import ExpirySet, ExpiryType, GlideClient

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

    async def get(self, vk: GlideClient, tab_id: str) -> T:
        data = await vk.get(self._key(tab_id))
        if data is None:
            return self._default_factory(tab_id)
        return msgspec.msgpack.decode(data, type=self._type)

    async def save(
        self, vk: GlideClient, tab_id: str, state: T, expire_seconds=60 * 60 * 24
    ) -> None:
        await vk.set(
            self._key(tab_id),
            msgspec.msgpack.encode(state),
            expiry=ExpirySet(ExpiryType.SEC, expire_seconds),
        )
        await vk.publish(b"1", self.channel(tab_id))
