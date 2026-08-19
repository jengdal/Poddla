import asyncio
import secrets

import msgspec
from asgiref.sync import sync_to_async
from datastar_py import ServerSentEventGenerator
from datastar_py.django import (
    DatastarResponse,
    read_signals,
)
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import aget_object_or_404
from django.template.loader import render_to_string

from podcasts.models import PodcastFeed, podcast_publisher
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
    if not signals:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = signals["tab_id"]

    vk = await valkey_client.get_client()
    max_fps = 1

    async def generator():
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
