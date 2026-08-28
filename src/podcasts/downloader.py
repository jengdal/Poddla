import asyncio
import logging
from pathlib import Path

from django.conf import settings

from podcasts.models import EpisodeDownload, download_publisher
from podcasts.youtube.video import download_audio
from valkey_changes.changes import changes

logger = logging.getLogger(__name__)

MAX_CONCURRENT_DOWNLOADS = settings.MAX_CONCURRENT_DOWNLOADS
MAX_CHANGE_WAIT = 120

_task: asyncio.Task | None = None


def _log_unexpected_exit(task: asyncio.Task) -> None:
    """The downloader task is never awaited while it runs, so nothing else would report this."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("The downloader stopped and downloads will not run.", exc_info=error)


def start() -> None:
    """Start the singleton downloader loop if it isn't already running."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(downloader())
    _task.add_done_callback(_log_unexpected_exit)


async def stop() -> None:
    """Cancel the downloader and wait for any downloads to complete."""
    global _task
    if _task is None:
        return
    _task.cancel()
    # return_exceptions so that a downloader which already failed doesn't take the shutdown down
    # with it. Whatever went wrong was reported by _log_unexpected_exit when it happened.
    await asyncio.gather(_task, return_exceptions=True)
    _task = None


async def _reset_stale_downloading() -> None:
    """Clear old downloads that might be left over after a crash.

    You should NOT run multiple instances of the downloader as this code will cause problems with
    multiple downloaders.
    """
    # Just delete them. Clients can retry them if they need them.
    await EpisodeDownload.objects.filter(status=EpisodeDownload.STATUS_DOWNLOADING).adelete()


async def _claim_next_batch(limit: int, exclude_ids: list[int]) -> list[EpisodeDownload]:
    claimed = []
    qs = (
        EpisodeDownload.objects.filter(status=EpisodeDownload.STATUS_PENDING)
        .exclude(id__in=exclude_ids)
        .select_related("episode")
        .order_by("index", "created_at")[:limit]
    )
    async for ed in qs:
        ed.status = EpisodeDownload.STATUS_DOWNLOADING
        await ed.asave(update_fields=["status"])
        claimed.append(ed)
    return claimed


async def _process_download(ed: EpisodeDownload) -> None:
    episode = ed.episode
    media_root = Path(settings.MEDIA_ROOT)
    rel_path = Path(str(episode.podcast_id)) / str(episode.id)
    try:
        download_info = await asyncio.to_thread(download_audio, episode.url, media_root, rel_path)
        episode.file_path = str(download_info.file_path.relative_to(media_root))
        episode.published_at = download_info.published_at
        episode.duration = download_info.duration
        episode.show_notes = str(download_info.description or "")
        # episode_publisher will notify listeners on save:
        await episode.asave(update_fields=("published_at", "duration", "show_notes", "file_path"))
    except asyncio.CancelledError:
        # Shutting down while the download is still incomplete. The EpisodeDownload will be
        # deleted on next start, the client may retry if it wishes to have the file.
        raise
    except Exception:
        logger.exception("Download failed for episode %s (EpisodeDownload %s)", episode.id, ed.id)
        ed.status = EpisodeDownload.STATUS_FAILED
        await ed.asave(update_fields=["status"])
    else:
        # Job's done — Episode.file_path is now the source of truth.
        await ed.adelete()


async def downloader() -> None:
    """This is the downloader loop.

    Listen for EpisodeDownload changes and download up to MAX_CONCURRENT_DOWNLOADS.
    """
    await _reset_stale_downloading()

    in_flight: dict[int, asyncio.Task] = {}

    # The loop below does a pass before it ever waits, so the first check happens immediately.
    async with changes(download_publisher.subscribe()) as changed:
        try:
            while True:
                for ed_id, task in list(in_flight.items()):
                    if task.done():
                        del in_flight[ed_id]

                free_slots = MAX_CONCURRENT_DOWNLOADS - len(in_flight)
                if free_slots > 0:
                    claimed = await _claim_next_batch(
                        limit=free_slots, exclude_ids=list(in_flight.keys())
                    )
                    for ed in claimed:
                        logger.debug(
                            "Starting download task for episode %s (EpisodeDownload %s)",
                            ed.episode_id,
                            ed.id,
                        )
                        in_flight[ed.id] = asyncio.create_task(_process_download(ed))

                try:
                    # We don't wait indefinitely. Make sure to check the tasks and the db
                    # periodically even if we received no notification.
                    await asyncio.wait_for(changed.wait(), timeout=MAX_CHANGE_WAIT)
                except TimeoutError:
                    logger.debug("Rechecking EpisodeDownloads.")
        except asyncio.CancelledError:
            for task in in_flight.values():
                task.cancel()
            if in_flight:
                await asyncio.gather(*in_flight.values(), return_exceptions=True)
            raise
