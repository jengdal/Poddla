import asyncio

import msgspec
from asgiref.sync import sync_to_async
from datastar_py import ServerSentEventGenerator
from datastar_py.django import (
    DatastarResponse,
    read_signals,
)
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string

from podcasts.models import PodcastFeed, podcast_publisher
from valkey_changes.changes import changes
from valkey_changes.state_store import StateStore


class PodcastsState(msgspec.Struct):
    tab_id: str


_store: StateStore[PodcastsState] = StateStore(
    PodcastsState,
    namespace="podcasts",
    default_factory=lambda tab_id: PodcastsState(tab_id=tab_id),
)


def sync_render_index(request: HttpRequest, state: PodcastsState):
    return render_to_string(
        request=request,
        template_name="podcasts/podcasts.html",
        context={
            "state": state,
            "podcast_feeds": PodcastFeed.objects.all().order_by("name"),
        },
    )


async def render_index(request: HttpRequest, state: PodcastsState):
    return await sync_to_async(sync_render_index)(request=request, state=state)


async def podcasts(request: HttpRequest):
    state = await _store.new(request.user.id)
    return HttpResponse(await render_index(request=request, state=state))


async def podcasts_sse(request: HttpRequest):
    signals = read_signals(request)
    max_fps = 1

    async def generator():
        if not signals:
            # Reload because this doesn't make sense.
            yield ServerSentEventGenerator.redirect("./")
            return
        tab_id = signals["tab_id"]
        event_id = 0

        tab = _store.subscribe(tab_id, request.user.id)
        async with changes(tab, podcast_publisher.subscribe()) as changed:
            while True:
                # Send the current state immediately, this primes the compression on the SSE stream:
                event_id += 1
                html = await render_index(request=request, state=tab.state)
                yield ServerSentEventGenerator.patch_elements(html, event_id=str(event_id))
                # Limit the FPS. When a lot of episodes are created we can get a lot of events at
                # once and don't want to create a new "frame" for each one:
                await asyncio.sleep(1.0 / max_fps)
                await changed.wait()

    return DatastarResponse(content=generator())
