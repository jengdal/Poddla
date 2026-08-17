import asyncio
import secrets

import msgspec
from asgiref.sync import sync_to_async
from datastar_py import ServerSentEventGenerator
from datastar_py.django import (
    DatastarResponse,
    read_signals,
)
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from podcasts.forms import ChannelForm
from podcasts.models import Channel
from youtube_to_podcast import valkey_client
from youtube_to_podcast.state_store import StateStore


class PodcastsState(msgspec.Struct):
    tab_id: str
    add_channel_data: dict[str, str | list[str]] | None = None
    add_channel_has_error: bool = False


_store: StateStore[PodcastsState] = StateStore(
    PodcastsState,
    namespace="podcasts",
    default_factory=lambda tab_id: PodcastsState(tab_id=tab_id),
)


def sync_render_index(request: HttpRequest, state: PodcastsState):
    if state.add_channel_has_error:
        form = ChannelForm(data=state.add_channel_data)
        if form.is_valid():
            # Just to trigger the validation that then produces the errors that get rendered.
            pass
    else:
        form = ChannelForm()

    return render_to_string(
        request=request,
        template_name="podcasts/podcasts.html",
        context={
            "tab_id": state.tab_id,
            "add_channel_form": form,
            "channels": Channel.objects.all().order_by("name"),
        },
    )


async def render_index(request: HttpRequest, state: PodcastsState):
    return await sync_to_async(sync_render_index)(request=request, state=state)


async def podcasts(request: HttpRequest):
    tab_id = secrets.token_urlsafe(16)
    vk = await valkey_client.get_client()
    state = await _store.get(vk, tab_id)
    return HttpResponse(await render_index(request=request, state=state))


async def podcasts_sse(request: HttpRequest):
    signals = read_signals(request)
    if not signals:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = signals["tab_id"]

    vk = await valkey_client.get_client()

    async def generator():
        event_id = 0
        sub = await valkey_client.create_subscriber(_store.channel(tab_id))
        try:
            # Send current state immediately on connect.
            state = await _store.get(vk, tab_id)
            event_id += 1
            html = await render_index(request=request, state=state)
            yield ServerSentEventGenerator.patch_elements(html, event_id=str(event_id))

            while True:
                await sub.get_pubsub_message()
                state = await _store.get(vk, tab_id)
                event_id += 1
                html = await render_index(request=request, state=state)
                yield ServerSentEventGenerator.patch_elements(
                    html, event_id=str(event_id)
                )
        finally:
            await sub.close()

    return DatastarResponse(content=generator())


@require_POST
async def add_channel(request: HttpRequest):
    # We post using datastars "form" contentType, it leaves out signals so we use a tab_id input element instead:
    tab_id = request.POST.get("tab_id", None)
    if not tab_id:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = str(tab_id)

    vk = await valkey_client.get_client()
    state = await _store.get(vk, tab_id)
    form = ChannelForm(data=request.POST)
    if form.is_valid():
        await sync_to_async(form.save)()
        state.add_channel_data = {}
        state.add_channel_has_error = False
    else:
        state.add_channel_has_error = True
        state.add_channel_data = dict(request.POST.items())
    await _store.save(vk, tab_id, state)
    return HttpResponse(status=204)
