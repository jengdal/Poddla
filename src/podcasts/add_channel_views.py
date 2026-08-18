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
from youtube_to_podcast import valkey_client
from youtube_to_podcast.state_store import StateStore


class AddChannelState(msgspec.Struct):
    tab_id: str
    data: dict[str, str | list[str]] | None = None
    has_error: bool = False


_store: StateStore[AddChannelState] = StateStore(
    AddChannelState,
    namespace="add_channel",
    default_factory=lambda tab_id: AddChannelState(tab_id=tab_id),
)


def sync_render_index(request: HttpRequest, state: AddChannelState):
    if state.data:
        form = ChannelForm(data=state.data)
        if form.is_valid():
            # Just to trigger the validation that then produces the errors that get rendered.
            pass
    else:
        form = ChannelForm()

    return render_to_string(
        request=request,
        template_name="podcasts/add_channel.html",
        context={
            "state": state,
            "form": form,
        },
    )


async def render_index(request: HttpRequest, state: AddChannelState):
    return await sync_to_async(sync_render_index)(request=request, state=state)


async def add_channel(request: HttpRequest):
    tab_id = secrets.token_urlsafe(16)
    vk = await valkey_client.get_client()
    state = await _store.get(vk, tab_id)
    return HttpResponse(await render_index(request=request, state=state))


async def add_channel_sse(request: HttpRequest):
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
async def set_state(request: HttpRequest):
    # We post using datastars "form" contentType, it leaves out signals so we use a tab_id input element instead:
    tab_id = request.POST.get("tab_id", None)
    if not tab_id:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = str(tab_id)
    save = "save" in request.GET

    vk = await valkey_client.get_client()
    state = await _store.get(vk, tab_id)
    state.data = dict(request.POST.items())
    form = ChannelForm(data=request.POST)
    if form.is_valid():
        if save:
            await sync_to_async(form.save)()
            state.data = {}
        state.has_error = False
    else:
        state.has_error = True
    await _store.save(vk, tab_id, state)
    return HttpResponse(status=204)
