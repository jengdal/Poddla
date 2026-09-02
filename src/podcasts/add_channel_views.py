import secrets
from datetime import datetime, timezone

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
from podcasts.models import Episode, PodcastFeed
from podcasts.youtube import FeedSource, fetch_feed
from valkey_changes import valkey_client
from valkey_changes.changes import changes
from valkey_changes.state_store import StateStore


class AddChannelState(msgspec.Struct):
    tab_id: str
    data: dict[str, str | list[str]] | None = None
    can_preview: bool = False
    can_save: bool = False
    podcast_id: int | None = None
    loading: bool = False


_store: StateStore[AddChannelState] = StateStore(
    AddChannelState,
    namespace="add_channel",
    default_factory=lambda tab_id: AddChannelState(tab_id=tab_id),
)


def create_podcast_feed_draft(feed: FeedSource) -> PodcastFeed:
    podcast = PodcastFeed.everything.create(
        status=PodcastFeed.STATUS_DRAFT,
        source_type=feed.source_type,
        url=feed.url,
        name=feed.title,
        description=feed.description or "",
        thumbnail=feed.thumbnail or "",
    )
    Episode.objects.bulk_create(
        [
            Episode(
                podcast=podcast,
                youtube_id=v.id,
                title=v.title,
                url=v.url,
                duration=v.duration,
                thumbnail=v.thumbnail or "",
                published_at=datetime.fromtimestamp(v.timestamp, tz=timezone.utc)
                if v.timestamp
                else None,
            )
            for v in feed.videos
        ]
    )
    return podcast


def publish_channel(channel_pk: int) -> PodcastFeed:
    channel = PodcastFeed.everything.get(pk=channel_pk, status=PodcastFeed.STATUS_DRAFT)
    channel.status = PodcastFeed.STATUS_PUBLIC
    channel.save()
    return channel


def sync_render_index(request: HttpRequest, state: AddChannelState):
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


async def render_index(request: HttpRequest, state: AddChannelState):
    return await sync_to_async(sync_render_index)(request=request, state=state)


async def add_channel(request: HttpRequest):
    tab_id = secrets.token_urlsafe(16)
    state = await _store.get(tab_id)
    return HttpResponse(await render_index(request=request, state=state))


async def add_channel_sse(request: HttpRequest):
    signals = read_signals(request)
    if not signals:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = signals["tab_id"]

    async def generator():
        event_id = 0
        tab = _store.subscribe(tab_id)
        # changes() reads the state as it starts, so the first pass through the loop is the
        # "send current state immediately on connect" one.
        async with changes(tab) as changed:
            while True:
                event_id += 1
                html = await render_index(request=request, state=tab.state)
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
    state = await _store.get(tab_id)
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
                await sync_to_async(publish_channel)(state.podcast_id)
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
                await _store.save(tab_id, state)
                feed = await fetch_feed(
                    url=form.cleaned_data["url"],
                    cache_valkey_client=vk,
                    cache_seconds=settings.YOUTUBE_META_CACHE_SECONDS,
                )
                podcast = await sync_to_async(create_podcast_feed_draft)(feed)
                state.can_save = True
                state.podcast_id = podcast.id
        else:
            state.can_save = False
            state.can_preview = False
            state.podcast_id = None
    finally:
        state.loading = False
        await _store.save(tab_id, state)

    return HttpResponse(status=204)
