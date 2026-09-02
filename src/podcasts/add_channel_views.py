import msgspec
from asgiref.sync import sync_to_async
from datastar_py import ServerSentEventGenerator
from datastar_py.django import (
    DatastarResponse,
    read_signals,
)
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from podcasts.forms import FeedForm
from podcasts.models import PodcastFeed
from podcasts.youtube import fetch_feed
from valkey_changes import valkey_client
from valkey_changes.changes import changes
from valkey_changes.state_store import StateStore


class AddChannelState(msgspec.Struct):
    """This holds the state for a browser tab on this page."""

    tab_id: str
    data: dict[str, str | list[str]] | None = None
    can_preview: bool = False
    can_save: bool = False
    podcast_id: int | None = None
    loading: bool = False


# The states are stored in valkey by StateStore. The SSE HTML renderer below
# can listen for changes to a tabs state and re-render when it changes.
_store: StateStore[AddChannelState] = StateStore(
    AddChannelState,
    namespace="add_channel",
    default_factory=lambda tab_id: AddChannelState(tab_id=tab_id),
)


def _sync_render(request: HttpRequest, state: AddChannelState):
    # HTML rendering in Django has to be done in a sync context, the
    # templatetags and any querysets executed from within the templating system
    # all assume sync.
    if state.data:
        form = FeedForm(data=state.data)
        if form.is_valid():
            pass
    else:
        form = FeedForm()

    channel = None
    if state.podcast_id is not None:
        channel = PodcastFeed.everything.prefetch_related("episodes").get(pk=state.podcast_id)

    return render_to_string(
        request=request,
        template_name="podcasts/add_channel.html",
        context={
            "state": state,
            "form": form,
            "channel": channel,
        },
    )


async def _render(request: HttpRequest, state: AddChannelState):
    return await sync_to_async(_sync_render)(request=request, state=state)


async def add_channel(request: HttpRequest):
    state = await _store.new(request.user.id)
    return HttpResponse(await _render(request=request, state=state))


async def add_channel_sse(request: HttpRequest):
    signals = read_signals(request)
    if not signals:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = signals["tab_id"]

    async def generator():
        event_id = 0
        tab = _store.subscribe(tab_id, request.user.id)
        # We'll get the current state immediately on subscribe. That means
        # we'll likely send down the exact HTML the browser has already
        # rendered, but the overhead is practically nothing and this way we
        # "prime" the brotli compression window "in the background", which
        # means the next real change will arrive at the client using less data.
        async with changes(tab) as changed:
            while True:
                event_id += 1
                html = await _render(request=request, state=tab.state)
                yield ServerSentEventGenerator.patch_elements(html, event_id=str(event_id))
                yield ServerSentEventGenerator.patch_signals({"loading": tab.state.loading})
                await changed.wait()

    return DatastarResponse(content=generator())


@require_POST
async def set_state(request: HttpRequest):
    # We post using datastars "form" contentType, it leaves out signals so we use a tab_id input
    # element instead:
    tab_id = request.POST.get("tab_id", None)
    if not tab_id:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = str(tab_id)
    save = "save" in request.GET
    preview = "preview" in request.GET

    vk = await valkey_client.get_client()
    state = await _store.get(tab_id, request.user.id)
    if request.POST.get("url", None):
        state.data = dict(request.POST.items())
    else:
        state.data = None
    form = FeedForm(data=state.data)
    saved_podcast_id: int | None = None
    try:
        if await sync_to_async(form.is_valid)():
            state.can_preview = True
            if save and state.podcast_id:
                await sync_to_async(PodcastFeed.drafts.publish)(state.podcast_id)
                saved_podcast_id = state.podcast_id
                state.data = {}
                state.podcast_id = None
                state.can_save = False
                state.can_preview = False
                return DatastarResponse(
                    content=(
                        ServerSentEventGenerator.redirect(
                            reverse("podcast_feed", args=(saved_podcast_id,))
                        ),
                    )
                )
            elif preview:
                state.loading = True
                await _store.save(tab_id, state, request.user.id)
                feed = await fetch_feed(
                    url=form.cleaned_data["url"],
                    cache_valkey_client=vk,
                    cache_seconds=settings.YOUTUBE_META_CACHE_SECONDS,
                )
                podcast = await sync_to_async(PodcastFeed.drafts.create_draft)(feed)
                state.can_save = True
                state.podcast_id = podcast.id
        else:
            state.can_save = False
            state.can_preview = False
            state.podcast_id = None
    finally:
        state.loading = False
        await _store.save(tab_id, state, request.user.id)

    return HttpResponse(status=204)
