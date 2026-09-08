import asyncio
import logging
import mimetypes
from pathlib import Path
from urllib.parse import quote

import msgspec
from asgiref.sync import sync_to_async
from datastar_py import ServerSentEventGenerator
from datastar_py.django import (
    DatastarResponse,
    read_signals,
)
from django.contrib.auth.decorators import login_not_required
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    StreamingHttpResponse,
)
from django.shortcuts import aget_object_or_404
from django.template.loader import render_to_string

from podcasts.downloader import download_episode_media, refresh_podcast_feed_task
from podcasts.models import (
    Episode,
    PodcastFeed,
    episode_publisher,
    podcast_publisher,
)
from user_settings.feed_auth import authenticate_feed_token
from valkey_changes.changes import changes
from valkey_changes.state_store import StateStore


class PodcastFeedState(msgspec.Struct):
    tab_id: str
    podcast_id: int = 0


logger = logging.getLogger(__name__)

_store: StateStore[PodcastFeedState] = StateStore(
    PodcastFeedState,
    namespace="podcast_feed",
    default_factory=lambda tab_id: PodcastFeedState(tab_id=tab_id),
)


def _sync_render(request: HttpRequest, state: PodcastFeedState, podcast: PodcastFeed):
    episodes = list(podcast.episodes.order_by("-published_at"))
    return render_to_string(
        request=request,
        template_name="podcasts/podcast_feed.html",
        context={
            "state": state,
            "podcast": podcast,
            "episodes": episodes,
        },
    )


async def _render(request: HttpRequest, state: PodcastFeedState, podcast: PodcastFeed):
    return await sync_to_async(_sync_render)(request=request, state=state, podcast=podcast)


async def podcast_feed(request: HttpRequest, podcast_id: int):
    podcast = await aget_object_or_404(PodcastFeed.objects, pk=podcast_id)
    state = await _store.new(request.user.id)
    state.podcast_id = podcast_id
    await _store.save(state.tab_id, state, request.user.id)
    return HttpResponse(await _render(request=request, state=state, podcast=podcast))


async def podcast_feed_sse(request: HttpRequest, podcast_id: int):
    if not await PodcastFeed.objects.filter(id=podcast_id).aexists():
        raise Http404
    signals = read_signals(request)
    max_fps = 1

    async def generator():
        try:
            if not signals:
                # Reload because this doesn't make sense.
                yield ServerSentEventGenerator.redirect("./")
                return
            tab_id = signals["tab_id"]
            event_id = 0
            update_task: asyncio.Task[None] | None = None
            tab = _store.subscribe(tab_id, request.user.id)
            async with changes(tab, podcast_publisher.subscribe()) as changed:
                while True:
                    # Send the current state immediately, this primes the compression on the SSE stream.
                    try:
                        podcast = await PodcastFeed.objects.aget(pk=podcast_id)
                    except PodcastFeed.DoesNotExist:
                        # The podcast has been deleted, we reload the page so that the user gets a 404:
                        yield ServerSentEventGenerator.redirect("./")
                        return

                    if await podcast.aneeds_updating():
                        # This starts a background task unless update_task is still running.
                        # The only purpose of update_task is to make sure this particular SSE connection
                        # doesn't start multiple concurrent tasks. There's also a process wide lock that ensures
                        # only one feed is updated at a time, so there's no risk the feed is
                        # updated multiple times concurrently.
                        # TODO: We should probably use a tasks queue for this instead, where failures and such
                        # can be recorded and surfaced to the user somehow. This will have to do for now tho.
                        if not update_task or update_task.done():
                            update_task = refresh_podcast_feed_task(podcast=podcast)
                        # If the task finds new episodes we'll be notified about it through `podcast_publisher`.

                    event_id += 1
                    html = await _render(request=request, state=tab.state, podcast=podcast)
                    yield ServerSentEventGenerator.patch_elements(html, event_id=str(event_id))
                    # Limit the FPS. When a lot of episodes are created we can get a lot of events at
                    # once and don't want to create a new "frame" for each one:
                    await asyncio.sleep(1.0 / max_fps)
                    try:
                        # The timeout ensures we render at least one frame every 30 seconds.
                        await asyncio.wait_for(changed.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        pass

                    # await changed.wait()
        except Exception:
            logger.exception("Error in the podcast feed sse view.")
            raise

    return DatastarResponse(content=generator())


@login_not_required
async def episode_media(request: HttpRequest, feed_token: str, episode_id: int):
    """This view serve episode audio.

    - We can't require the normal auth flow here as I doubt any podcast apps
      would support that, instead we include a secret token in the URL.
    - If the file already exists on our filesystem, it is served directly.
    - If we need to download the file from YT we use a process wide lock to
      ensure we only download a single file at a time.
    """
    if await authenticate_feed_token(feed_token) is None:
        raise Http404

    episode = await aget_object_or_404(Episode, pk=episode_id)
    episode_file = await episode.file_exists()
    if not episode_file:
        # `download_episode_media` makes sure the file is only downloaded once.
        task = await download_episode_media(episode=episode)

        async with changes(episode_publisher.subscribe(pk=episode.id)) as changed:
            # Either our or another client's task will download the file, so we wait for
            # both the download task and episode changes.
            while True:
                episode = await Episode.objects.aget(id=episode.id)
                episode_file = await episode.file_exists()
                if episode_file:
                    if not task.done():
                        # Another clients task downloaded it.
                        task.cancel("File already exists.")
                    break
                if task.done():
                    break

                changed_wait = asyncio.create_task(changed.wait())
                try:
                    await asyncio.wait({task, changed_wait}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    # This is the sort of stuff that makes working with asyncio a pain.
                    if not changed_wait.done():
                        changed_wait.cancel()
                        try:
                            await changed_wait
                        except asyncio.CancelledError:
                            pass

            if task.done() and not task.cancelled():
                # Raise any exception that happened in the task:
                task.result()

    if not episode_file:
        logger.error("The Episode (%s) file was not downloaded.", episode.id)
        return HttpResponse(status=500)

    # Granian supports ASGI `pathsend`, which would let us hand off the file
    # path to it, and have it serve the file to the client. The problem is that
    # `pathsend` doesn't support `Range` requests, which would mean streaming
    # audio to podcast clients wouldn't work. But since we're anyway always
    # behind Caddy, we can hand off the file path to it instead. Caddy supports
    # `Range` requests just fine.
    # Note that _every single range request_ a streaming client makes will hit
    # this django view.
    response = HttpResponse(status=200)
    # Note that we use the relative path, not the full `episode_file`:
    response["X-Accel-Redirect"] = quote(f"/{episode.file_path}")
    return response
