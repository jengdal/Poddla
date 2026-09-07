import msgspec
from asgiref.sync import sync_to_async
from datastar_py import ServerSentEventGenerator
from datastar_py.django import (
    DatastarResponse,
    read_signals,
)
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import mark_safe
from django.views.decorators.http import require_POST

from poddla_settings.forms import FeedUpdateForm, MediaFilesExpiryForm
from poddla_settings.models import PoddlaSettings, settings_publisher
from poddla_settings.state import FeedUpdateState, MediaFilesState
from valkey_changes.changes import changes
from valkey_changes.state_store import StateStore

_feed_update_store: StateStore[FeedUpdateState] = StateStore(
    FeedUpdateState,
    namespace="poddla_settings_feed_update",
    default_factory=lambda tab_id: FeedUpdateState(tab_id=tab_id),
)

_media_files_store: StateStore[MediaFilesState] = StateStore(
    MediaFilesState,
    namespace="poddla_settings_media_files",
    default_factory=lambda tab_id: MediaFilesState(tab_id=tab_id),
)


def _sync_render(
    request: HttpRequest, feed_update_state: FeedUpdateState, media_files_state: MediaFilesState
) -> str:
    # HTML rendering in Django has to be done in a sync context, the
    # templatetags and any querysets executed from within the templating system
    # all assume sync.
    feed_update_form = FeedUpdateForm(data=feed_update_state.data or {})
    media_files_form = MediaFilesExpiryForm(data=media_files_state.data or {})

    # Run the validation on the forms so that we get errors to show:
    feed_update_form.is_valid()
    media_files_form.is_valid()

    current_settings = PoddlaSettings.objects.get_settings()
    feed_update_current = FeedUpdateForm.initial_from_settings(current_settings)
    media_files_current = MediaFilesExpiryForm.initial_from_settings(current_settings)
    csrf_token = get_token(request)

    signals = {
        "feed_update": feed_update_state.to_signals(),
        "media_files": media_files_state.to_signals(),
    }
    feed_update_context = {
        "form": feed_update_form,
        "state": feed_update_state,
        "current": feed_update_current,
        "post_call": mark_safe(
            # This is all the url is, it just looks horrible with the double {'s and function call:
            # @post('url', {'headers': {'x-csrftoken': ''}})
            # TODO: Look into using the datastar-py library to generate these
            f"@post('{reverse('settings_set_feed_update_state')}', {{'headers': {{'x-csrftoken': '{csrf_token}'}}}})"
        ),
    }
    media_files_context = {
        "form": media_files_form,
        "state": media_files_state,
        "current": media_files_current,
        "enabled": current_settings.media_files_expiry_enabled,
        "post_call": mark_safe(
            f"@post('{reverse('settings_set_media_files_state')}', {{'headers': {{'x-csrftoken': '{csrf_token}'}}}})"
        ),
    }
    return render_to_string(
        request=request,
        template_name="poddla_settings/settings.html",
        context={
            "state_json": mark_safe(msgspec.json.encode(signals).decode("utf-8")),
            "feed_update_context": feed_update_context,
            "media_files_context": media_files_context,
        },
    )


async def _render(
    request: HttpRequest, feed_update_state: FeedUpdateState, media_files_state: MediaFilesState
) -> str:
    return await sync_to_async(_sync_render)(
        request=request, feed_update_state=feed_update_state, media_files_state=media_files_state
    )


async def settings(request: HttpRequest):
    feed_update_state = await _feed_update_store.new(request.user.id)
    media_files_state = await _media_files_store.new(request.user.id)
    return HttpResponse(
        (
            await _render(
                request=request,
                feed_update_state=feed_update_state,
                media_files_state=media_files_state,
            )
        ).encode("utf-8")
    )


async def settings_sse(request: HttpRequest):
    signals = read_signals(request)
    if not signals:
        raise Exception("The view was called without signals.")
    # tab_id = signals["tab_id"]
    feed_update_id = signals.get("feed_update", {}).get("tab_id", None)
    media_files_id = signals.get("media_files", {}).get("tab_id", None)
    if not feed_update_id or not media_files_id:
        # This isn't really something that should happen if the SSE endpoint is called normally.
        raise Exception("The view was called without signals.")

    async def generator():
        event_id = 0
        # Listen to both the tab state and the settings in the database. The
        # template shows the live settings by default but when a form is show,
        # the values in the form inputs are owned by the tab and won't get
        # changed from under the user if another user makes an edit.
        # NOTE: that this is ofc overkill for a settings page in a self-hosted
        # service, but I'm making this to explore how to make this sort of UI
        # with Django and Datastar.
        tab_feed = _feed_update_store.subscribe(feed_update_id, request.user.id)
        tab_media = _media_files_store.subscribe(media_files_id, request.user.id)
        # We'll get the current state immediately on subscribe. That means
        # we'll likely send down the exact HTML the browser has already
        # rendered, but the overhead is practically nothing and this way we
        # "prime" the compression window "in the background", which means the
        # next real change will arrive at the client using less data.
        async with changes(tab_feed, tab_media, settings_publisher.subscribe()) as changed:
            while True:
                event_id += 1
                html = await _render(
                    request=request,
                    feed_update_state=tab_feed.state,
                    media_files_state=tab_media.state,
                )
                yield ServerSentEventGenerator.patch_elements(html, event_id=str(event_id))
                s = {
                    "feed_update": tab_feed.state.to_signals(),
                    "media_files": tab_media.state.to_signals(),
                }
                yield ServerSentEventGenerator.patch_signals(signals=s)
                await changed.wait()

    return DatastarResponse(content=generator())


@require_POST
async def set_feed_update_state(request: HttpRequest):
    signals = read_signals(request) or {}
    signals = signals.get("feed_update") or {}
    if not signals:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = signals["tab_id"]

    state = await _feed_update_store.get(tab_id, request.user.id)
    state.save = signals.get("save", False)

    try:
        state.show_form = bool(signals.get("show_form"))
        if not state.show_form:
            state.data = None
        elif state.data is None:
            current_settings = await PoddlaSettings.objects.aget_settings()
            state.data = FeedUpdateForm.initial_from_settings(current_settings)
        else:
            state.data = signals.get("data", {})

        # state.sync_data(signals=feed_update_signals, current_settings=current_settings)
        if state.show_form:
            form = FeedUpdateForm(data=state.data)
            state.can_save = await sync_to_async(form.is_valid)()
            if state.can_save and state.save:
                await PoddlaSettings.objects.aupdate_or_create(
                    pk=1, defaults=form.to_model_kwargs()
                )
                state.show_form = False
                state.data = None
                state.can_save = False

    finally:
        state.loading = False
        state.save = False
        await _feed_update_store.save(tab_id, state, request.user.id)

    return HttpResponse(status=204)


@require_POST
async def set_media_files_state(request: HttpRequest):
    signals = read_signals(request) or {}
    signals = signals.get("media_files") or {}
    if not signals:
        # TODO: Add this to the state and show a toast error or something.
        raise Exception()
    tab_id = signals["tab_id"]

    state = await _media_files_store.get(tab_id, request.user.id)
    state.save = signals.get("save", False)

    try:
        state.show_form = bool(signals.get("show_form"))
        if not state.show_form:
            # The form is not shown. We clear state.data so that:
            # - we don't remember any ambandoned input
            # - we can recognice the empty state and fill in the latest from the db if the user opens the form again
            state.data = None
        elif state.data is None:
            # The form was opened now, we fill it in with data from the db.
            current_settings = await PoddlaSettings.objects.aget_settings()
            state.data = MediaFilesExpiryForm.initial_from_settings(current_settings)
            state.enabled = current_settings.media_files_expiry_enabled
        else:
            # The form was already open, we update the state based on the signals we received.
            state.data = signals.get("data", {})
            state.enabled = bool(signals.get("enabled", False))

        if state.show_form:
            update = None
            if state.enabled:
                form = MediaFilesExpiryForm(data=state.data)
                state.can_save = await sync_to_async(form.is_valid)()
                if state.can_save:
                    update = form.to_model_kwargs()
            else:
                # When expiry is disabled we don't care about the rest of the
                # form, it may even contain invalid values.
                update = {"media_files_expiry_enabled": False}
                state.can_save = True

            if update and state.save:
                await PoddlaSettings.objects.aupdate_or_create(pk=1, defaults=update)
                state.show_form = False
                state.data = None
                state.can_save = False

    finally:
        state.loading = False
        state.save = False
        await _media_files_store.save(tab_id, state, request.user.id)

    return HttpResponse(status=204)
