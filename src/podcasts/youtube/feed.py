import logging
from typing import Any, Literal

import msgspec
import yt_dlp
from glide import GlideClient

from podcasts.youtube._cache import YOUTUBE_CACHE_KEY_PREFIX, run_cached

logger = logging.getLogger(__name__)


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


def _extract_info(url: str, entries_limit: int | None) -> dict:
    opts: dict[str, Any] = {
        # TODO: Pass a custom logger object.
        # "verbose": True,
        # "quiet": False,
        # "no_warnings": False,
        "quiet": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "extractor_args": {"youtubetab": {"approximate_date": ["True"]}},
    }
    if entries_limit:
        # Only fetch this many "entries", videos etc.
        opts["playlistend"] = entries_limit
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _get_thumbnail(thumbnails: list[dict] | None) -> str | None:
    if not thumbnails:
        return None
    return thumbnails[-1].get("url")


def _yt_cache_key(url: str, entries_limit: int | None):
    return f"{YOUTUBE_CACHE_KEY_PREFIX}{url}:{entries_limit}"


async def fetch_feed(
    url: str,
    entries_limit: int | None = None,
    cache_valkey_client: GlideClient | None = None,
    cache_seconds: int = 3600,
) -> FeedSource:
    original_url = url
    info = await run_cached(
        _yt_cache_key(url, entries_limit),
        lambda: _extract_info(url, entries_limit=entries_limit),
        cache_valkey_client,
        cache_seconds,
    )

    title = info.get("title", "")
    entries = list(info.get("entries") or [])

    # The root of a channel URL returns playlist entries, we have to use the Video tab:
    if entries and entries[0].get("_type") == "playlist":
        videos_tab = next(
            (e for e in entries if "Videos" in (e.get("title") or "")),
            entries[0],
        )
        url = str(videos_tab["webpage_url"])
        if not url:
            raise Exception(f"Could not find the URL to the Videos tab on {original_url}")
        logger.debug("Using %s instead of %s.", url, original_url)
        info = await run_cached(
            _yt_cache_key(url, entries_limit),
            lambda: _extract_info(url, entries_limit=entries_limit),
            cache_valkey_client,
            cache_seconds,
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
            thumbnail=_get_thumbnail(e.get("thumbnails")),
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
