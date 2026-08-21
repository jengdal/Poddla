import asyncio

import msgspec
from glide import ExpirySet, ExpiryType, GlideClient

YOUTUBE_CACHE_KEY_PREFIX = "youtube:extract_info:"


async def run_cached(
    cache_key: str,
    sync_fn,
    vk: GlideClient | None,
    cache_seconds: int,
) -> dict:
    if vk is None:
        return await asyncio.to_thread(sync_fn)
    cached = await vk.get(cache_key)
    if cached is not None:
        try:
            return msgspec.msgpack.decode(cached)
        except Exception:
            pass
    info = await asyncio.to_thread(sync_fn)
    await vk.set(
        cache_key,
        msgspec.msgpack.encode(info),
        expiry=ExpirySet(ExpiryType.SEC, cache_seconds),
    )
    return info
