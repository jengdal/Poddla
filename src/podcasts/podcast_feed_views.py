import asyncio
import mimetypes
import secrets
from pathlib import Path

import msgspec
from asgiref.sync import sync_to_async
from datastar_py import ServerSentEventGenerator
from datastar_py.django import (
    DatastarResponse,
    read_signals,
)
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    StreamingHttpResponse,
)
from django.shortcuts import aget_object_or_404
from django.template.loader import render_to_string

from podcasts.models import (
    Episode,
    EpisodeDownload,
    PodcastFeed,
    download_publisher,
    podcast_publisher,
)
from poddla import valkey_client
from poddla.state_store import StateStore


class PodcastFeedState(msgspec.Struct):
    tab_id: str
    podcast_id: int = 0


_store: StateStore[PodcastFeedState] = StateStore(
    PodcastFeedState,
    namespace="podcast_feed",
    default_factory=lambda tab_id: PodcastFeedState(tab_id=tab_id),
)


def sync_render_index(
    request: HttpRequest, state: PodcastFeedState, podcast: PodcastFeed
):
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


async def render_index(
    request: HttpRequest, state: PodcastFeedState, podcast: PodcastFeed
):
    return await sync_to_async(sync_render_index)(
        request=request, state=state, podcast=podcast
    )


async def podcast_feed(request: HttpRequest, podcast_id: int):
    podcast = await aget_object_or_404(PodcastFeed.objects, pk=podcast_id)
    tab_id = secrets.token_urlsafe(16)
    vk = await valkey_client.get_client()
    state = PodcastFeedState(tab_id=tab_id, podcast_id=podcast_id)
    await _store.save(vk, tab_id, state)
    return HttpResponse(
        await render_index(request=request, state=state, podcast=podcast)
    )


async def podcast_feed_sse(request: HttpRequest, podcast_id: int):
    if not await PodcastFeed.objects.filter(id=podcast_id).aexists():
        raise Http404
    signals = read_signals(request)

    vk = await valkey_client.get_client()
    max_fps = 1

    async def generator():
        if not signals:
            # Reload because this doesn't make sense.
            yield ServerSentEventGenerator.redirect("./")
            return
        tab_id = signals["tab_id"]
        event_id = 0
        dirty = asyncio.Event()

        def on_message(msg, ctx):
            dirty.set()

        # We use glide's callback mode because we only care about the latest message, and the
        # polling mode keeps an unbounded list of all messages, which is not at all what we need:
        # https://glide.valkey.io/how-to/publish-and-subscribe-messages/#receiving-messages
        sub_state = await valkey_client.create_subscriber(
            _store.channel(tab_id), callback=on_message
        )
        sub_model = await valkey_client.create_subscriber(
            podcast_publisher.channel, callback=on_message
        )

        try:
            while True:
                # Send the current state immediately, this primes the compression on the SSE stream.
                state = await _store.get(vk, tab_id)

                try:
                    podcast = await PodcastFeed.objects.aget(pk=podcast_id)
                except PodcastFeed.DoesNotExist:
                    # The podcast has been deleted, we reload the page so that the user gets a 404:
                    yield ServerSentEventGenerator.redirect("./")
                    return

                event_id += 1
                html = await render_index(request=request, state=state, podcast=podcast)
                yield ServerSentEventGenerator.patch_elements(
                    html, event_id=str(event_id)
                )
                # Limit the FPS. When a lot of episodes are created we can get a lot of events at
                # once and don't want to create a new "frame" for each one:
                await asyncio.sleep(1.0 / max_fps)
                await dirty.wait()
                dirty.clear()
        finally:
            await sub_state.close()
            await sub_model.close()

    return DatastarResponse(content=generator())


async def episode_media(request: HttpRequest, episode_id: int):
    """This view serve episode audio.

    - If the file already exists on our filesystem, it is served directly.
    - Else we tell the downloader it needs to download the file by creating
      or updating a EpisodeDownload row, and wait util the file has been
      downloaded.
    - Multiple simultaneous clients requesting the same file will wait for the
      same download.
    - If a client disconnects, the downloader will still complete the download.
    """
    episode = await aget_object_or_404(Episode, pk=episode_id)
    episode_file = episode.file_exists()
    if not episode_file:
        download, created = await EpisodeDownload.objects.aget_or_create(
            episode_id=episode_id
        )
        if not created and download.status == EpisodeDownload.STATUS_FAILED:
            download.status = EpisodeDownload.STATUS_PENDING
            # TODO: There's a race-condition here, another client might have changed it to pending
            # already, and the downloader could even have finished it theoretically, very unlikely
            # tho.
            await download.asave(update_fields=["status"])

        event = asyncio.Event()

        def on_update(msg, ctx):
            ctx.set()

        download_sub = await valkey_client.create_subscriber(
            download_publisher.channel_for(download.pk),
            callback=on_update,
            context=event,
        )

        try:
            while True:
                episode = await Episode.objects.aget(id=episode_id)
                episode_file = episode.file_exists()
                if episode_file:
                    break
                try:
                    download = await EpisodeDownload.objects.aget(episode_id=episode_id)
                except EpisodeDownload.DoesNotExist:
                    # This could happen if the episode finished after we checked the episode file
                    # but before we checked the download. The download row is deleted when the
                    # download finishes. So, recheck immediately:
                    continue
                if download.status == EpisodeDownload.STATUS_FAILED:
                    # We don't auto retry the download while waiting, the client will have to retry
                    # the request.
                    return HttpResponse(status=500)
                # When the download status is PENDING or DOWNLOADING we continue the while loop.

                try:
                    await asyncio.wait_for(event.wait(), timeout=60 * 10)
                except TimeoutError:
                    return HttpResponse(status=504)
                event.clear()

        finally:
            await download_sub.close()

    # TODO: Use nginx or caddy to serve the file instead:
    return _serve_with_range(request, episode_file)


def _serve_with_range(request: HttpRequest, filepath: Path) -> HttpResponse:
    file_size = filepath.stat().st_size
    content_type = mimetypes.guess_type(str(filepath))[0] or "application/octet-stream"
    range_header = request.headers.get("Range")

    if not range_header:
        response = FileResponse(filepath.open("rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        return response

    range_spec = range_header.strip().removeprefix("bytes=")
    start_str, _, end_str = range_spec.partition("-")
    start = int(start_str) if start_str else 0
    end = int(end_str) if end_str else file_size - 1
    end = min(end, file_size - 1)
    length = end - start + 1

    def read_range():
        with filepath.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    response = StreamingHttpResponse(
        read_range(), status=206, content_type=content_type
    )
    response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    response["Content-Length"] = str(length)
    response["Accept-Ranges"] = "bytes"
    return response
