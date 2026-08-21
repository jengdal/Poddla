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
from django.conf import settings
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    StreamingHttpResponse,
)
from django.shortcuts import aget_object_or_404
from django.template.loader import render_to_string

from podcasts.models import Episode, PodcastFeed, podcast_publisher
from podcasts.youtube.video import download_audio
from youtube_to_podcast import valkey_client
from youtube_to_podcast.state_store import StateStore


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
                # Limit the FPS. When a lot of episodes are created we can get a lot of events at once and
                # don't want to create a new "frame" for each one:
                await asyncio.sleep(1.0 / max_fps)
                await dirty.wait()
                dirty.clear()
        finally:
            await sub_state.close()
            await sub_model.close()

    return DatastarResponse(content=generator())


async def episode_media(request: HttpRequest, episode_id: int):
    episode = await aget_object_or_404(Episode, pk=episode_id)
    media_root = Path(settings.MEDIA_ROOT)

    episode_file: Path | None = None
    if episode.file_path:
        episode_file = media_root / episode.file_path

    if episode_file is None or not episode_file.exists():
        rel_path = Path(str(episode.podcast_id)) / str(episode.id)
        download_info = await asyncio.to_thread(
            download_audio, episode.url, media_root, rel_path
        )
        # The downloader appends a file extension to the filename we gave it, so we need to use the update one:
        episode_file = download_info.file_path

        episode.file_path = str(download_info.file_path.relative_to(media_root))
        episode.published_at = download_info.published_at
        episode.duration = download_info.duration
        episode.show_notes = str(download_info.description or "")

        # Use save instead of update so that we trigger the podcast_publisher automatically throught the save signal
        await episode.asave(
            update_fields=("published_at", "duration", "show_notes", "file_path")
        )

    # TODO: Use nginx to serve the file instead:
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
