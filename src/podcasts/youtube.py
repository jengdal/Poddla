import asyncio
from typing import Literal

import msgspec
import yt_dlp
from glide import ExpirySet, ExpiryType, GlideClient


class VideoInfo(msgspec.Struct):
    id: str
    title: str
    url: str
    duration: int | None = None
    view_count: int | None = None
    thumbnail: str | None = None
    timestamp: int | None = None


class FeedSource(msgspec.Struct):
    url: str
    title: str
    source_type: Literal["channel", "playlist"]
    videos: list[VideoInfo]
    channel_id: str | None = None
    description: str | None = None
    uploader: str | None = None
    channel_url: str | None = None
    thumbnail: str | None = None
    follower_count: int | None = None
    video_count: int | None = None


YOUTUBE_CACHE_KEY_PREFIX = "youtube:extract_info:"


def _extract_info(url: str) -> dict:
    opts = {
        "quiet": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "extractor_args": {"youtubetab": {"approximate_date": ["True"]}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


async def _extract_info_cached(
    url: str, vk: GlideClient | None, cache_seconds: int
) -> dict:
    if vk is None:
        return await asyncio.to_thread(_extract_info, url)
    cache_key = f"{YOUTUBE_CACHE_KEY_PREFIX}{url}"
    cached = await vk.get(cache_key)
    if cached is not None:
        try:
            return msgspec.msgpack.decode(cached)
        except Exception:
            pass
    info = await asyncio.to_thread(_extract_info, url)
    await vk.set(
        cache_key,
        msgspec.msgpack.encode(info),
        expiry=ExpirySet(ExpiryType.SEC, cache_seconds),
    )
    return info


def _get_thumbnail(thumbnails: list[dict] | None) -> str | None:
    if not thumbnails:
        return None
    return thumbnails[-1].get("url")


async def fetch_feed(
    url: str,
    cache_valkey_client: GlideClient | None = None,
    cache_seconds: int = 3600,
) -> FeedSource:
    info = await _extract_info_cached(url, cache_valkey_client, cache_seconds)

    title = info.get("title", "")
    entries = list(info.get("entries") or [])

    # The root of a channel URL returns playlist entries, we have to use the Video tab:
    if entries and entries[0].get("_type") == "playlist":
        videos_tab = next(
            (e for e in entries if "Videos" in (e.get("title") or "")),
            entries[0],
        )
        info = await _extract_info_cached(
            videos_tab["webpage_url"], cache_valkey_client, cache_seconds
        )
        entries = list(info.get("entries") or [])

    source_type: Literal["channel", "playlist"] = (
        "playlist" if info.get("webpage_url_basename") == "playlist" else "channel"
    )

    videos = [
        VideoInfo(
            id=e["id"],
            title=e.get("title") or "",
            url=e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
            duration=e.get("duration"),
            view_count=e.get("view_count"),
            thumbnail=_best_thumbnail(e.get("thumbnails")),
            timestamp=e.get("timestamp"),
        )
        for e in entries
        if e.get("id")
    ]

    return FeedSource(
        url=url,
        title=title,
        source_type=source_type,
        videos=videos,
        channel_id=info.get("channel_id"),
        description=info.get("description"),
        uploader=info.get("uploader"),
        channel_url=info.get("channel_url"),
        thumbnail=_get_thumbnail(info.get("thumbnails")),
        follower_count=info.get("channel_follower_count"),
        video_count=info.get("playlist_count"),
    )
