import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

from podcasts.asyncio_utils import start_task, wait_lock_or_func
from podcasts.models import (
    Episode,
    PodcastFeed,
    episode_publisher,
)
from podcasts.youtube import fetch_feed
from podcasts.youtube.video import download_audio
from valkey_changes import valkey_client
from valkey_changes.changes import changes

# When refreshing a YT channel we fetch at most this many video entries
# initially. If we only find new entries within those, we fetch more.
YOUTUBE_CHANNEL_REFRESH_INITIAL_LIMIT = 30
# I guess we need some sort of limit for extreme cases:
YOUTUBE_CHANNEL_REFRESH_LIMIT = 5000

logger = logging.getLogger(__name__)

# We throttle YT requests, we only allow:
# - A single download _from YT_ at a time
# - A single channel/playlist metadata download _from YT_ at a time.
#
# NOTE: however that users of Poddla can still download/stream multiple
# concurrent audio files and podcast feeds from Poddla itself, as they are
# cached in Poddla. It is only the requests to YT that are throttled. I think
# this limitation is perfectly fine for a self-hosted service meant for
# personal use. I think it's sensible to not send too many concurrent requests
# to YT for obvious reasons.
#
# The locking mechanism relies on there only being a single Poddla worker
# process, which is the case if you follow the deploy instructions. I may
# refactor this in the future to make it possible to allow more than one
# download at a time, but that will probably require a task queue.
_audio_download_lock = asyncio.Lock()
_feed_download_lock = asyncio.Lock()


def refresh_podcast_feed_task(podcast: PodcastFeed) -> asyncio.Task[None]:
    return start_task(
        refresh_podcast_feed(podcast=podcast),
        on_error="The podcast feed update task failed.",
    )


async def refresh_podcast_feed(podcast: PodcastFeed) -> None:
    async with _feed_download_lock:
        podcast = await PodcastFeed.objects.aget(pk=podcast.pk)
        # Re-checking if it needs updating since another task might have
        # updated it while we waited for the lock:
        if not await podcast.aneeds_updating():
            return
        await _refresh_podcast_feed(podcast=podcast)


def refresh_podcast_feed_full_task(podcast: PodcastFeed) -> asyncio.Task[None]:
    return start_task(
        _refresh_podcast_feed_full(podcast=podcast),
        on_error="The podcast feed full refresh task failed.",
    )


async def _refresh_podcast_feed_full(podcast: PodcastFeed) -> None:
    async with _feed_download_lock:
        podcast = await PodcastFeed.objects.aget(pk=podcast.pk)
        await _fetch_and_update(podcast=podcast, entries_limit=YOUTUBE_CHANNEL_REFRESH_LIMIT)


async def _fetch_and_update(podcast: PodcastFeed, entries_limit: int):
    found_old_ep = False

    feed = await fetch_feed(
        url=str(podcast.url),
        cache_valkey_client=await valkey_client.get_client(),
        cache_seconds=settings.YOUTUBE_META_CACHE_SECONDS,
        entries_limit=entries_limit,
    )

    podcast.name = feed.title
    podcast.description = feed.description or ""
    podcast.thumbnail = feed.thumbnail or ""
    await podcast.asave(update_fields=["name", "description", "thumbnail", "updated_at"])

    for v in feed.videos:
        published_at = datetime.fromtimestamp(v.timestamp, tz=timezone.utc) if v.timestamp else None
        thumbnail = v.thumbnail or ""

        _, created = await Episode.objects.aupdate_or_create(
            podcast=podcast,
            url=v.url,
            defaults={"title": v.title, "thumbnail": thumbnail},
            create_defaults={
                "title": v.title,
                "youtube_id": v.id,
                "thumbnail": thumbnail,
                # These are not very accurate when gotten from the channel or playlist. When we
                # download media, we also get more accurate data for these and update them at
                # that point, so don't overwrite potentially better data here:
                "duration": v.duration,
                "published_at": published_at,
            },
        )
        if not created:
            found_old_ep = True
    return found_old_ep, len(feed.videos)


async def _refresh_podcast_feed(podcast: PodcastFeed) -> None:
    logger.debug("Refreshing PodcastFeed (%s)", podcast.id)
    found_old_ep, found_count = await _fetch_and_update(
        podcast=podcast, entries_limit=YOUTUBE_CHANNEL_REFRESH_INITIAL_LIMIT
    )
    if found_count and not found_old_ep:
        logger.debug(
            f"Refreshing PodcastFeed (%s): Did not find an old episode within the newest {YOUTUBE_CHANNEL_REFRESH_INITIAL_LIMIT} entries, which means we're now fetching more, gotta catch them all.",
            podcast.id,
        )
        # TODO: make the limit configurable on the podcast model:
        await _fetch_and_update(podcast=podcast, entries_limit=YOUTUBE_CHANNEL_REFRESH_LIMIT)
    logger.debug("Done refreshing PodcastFeed (%s)", podcast.id)


async def download_episode_media(episode: Episode) -> Episode:
    episode_file = episode.file_exists()
    if episode_file:
        return episode
    async with changes(episode_publisher.subscribe(pk=episode.id)) as changed:
        while not episode_file:
            outcome, _ = await wait_lock_or_func(_audio_download_lock, changed.wait())
            handed_off = False
            try:
                episode = await Episode.objects.aget(id=episode.id)
                episode_file = episode.file_exists()
                if not episode_file and outcome == "lock":
                    # If we get cancelled the lock would immediately be released by the
                    # `finally` below, but the yt-dlp download would still continue until
                    # it finished. This means the lock mechanism would basically be broken,
                    # we could have multiple concurrent downloads just because clients
                    # disconnected. We take care of that by letting the task release when
                    # it's done:
                    handed_off = True
                    return await asyncio.shield(asyncio.create_task(_download_and_release(episode)))
            finally:
                if outcome == "lock" and not handed_off:
                    _audio_download_lock.release()

    return episode


async def _download_and_release(episode: Episode) -> Episode:
    try:
        return await _download_and_update(episode)
    finally:
        _audio_download_lock.release()


async def _download_and_update(episode: Episode) -> Episode:
    media_root = Path(settings.MEDIA_ROOT)
    rel_path = Path("audio") / Path(str(episode.podcast_id)) / str(episode.id)
    download_info = await asyncio.to_thread(download_audio, episode.url, media_root, rel_path)
    episode.file_path = str(download_info.file_path.relative_to(media_root))
    episode.published_at = download_info.published_at
    episode.duration = download_info.duration
    episode.show_notes = str(download_info.description or "")
    # podcast_publisher will notify listeners on save:
    await episode.asave(update_fields=("published_at", "duration", "show_notes", "file_path"))
    return episode
