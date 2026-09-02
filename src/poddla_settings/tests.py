import datetime
import json

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import AsyncRequestFactory, TransactionTestCase, override_settings

from poddla_settings.models import PoddlaSettings
from poddla_settings.views import (
    _feed_update_store,
    _media_files_store,
    set_feed_update_state,
    set_media_files_state,
)
from valkey_changes import valkey_client


async def _flush_valkey():
    vk = await valkey_client.get_client()
    await vk.flushdb()


@override_settings(VALKEY_DB=15)
class SetStateTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listener", password="pw")
        self.feed_update_tab_id = f"{self.user.id}:test-tab-feed-update"
        self.media_files_tab_id = f"{self.user.id}:test-tab-media-files"
        self.factory = AsyncRequestFactory()
        async_to_sync(_flush_valkey)()

    def tearDown(self):
        async_to_sync(_flush_valkey)()

    def _post(self, view, section: str, tab_id: str, signals: dict):
        body = {section: {"tab_id": tab_id, "save": False, **signals}}
        req = self.factory.post(
            "/settings/state/",
            data=json.dumps(body),
            content_type="application/json",
            headers={"Datastar-Request": "true"},
        )
        req.user = self.user
        return view(req)

    def _post_feed_update(self, signals: dict):
        return self._post(set_feed_update_state, "feed_update", self.feed_update_tab_id, signals)

    def _post_media_files(self, signals: dict):
        return self._post(set_media_files_state, "media_files", self.media_files_tab_id, signals)

    async def _get_feed_update_state(self):
        return await _feed_update_store.get(self.feed_update_tab_id, self.user.id)

    async def _get_media_files_state(self):
        return await _media_files_store.get(self.media_files_tab_id, self.user.id)

    async def test_missing_signals_raises(self):
        # Pre-existing behavior, not something this change alters: a POST
        # without Datastar's signal payload has nothing to key the tab's
        # state off, so read_signals returns None and the view raises.
        req = self.factory.post("/settings/state/")
        req.user = self.user

        with self.assertRaises(Exception):  # noqa: B017 - the view itself raises a bare Exception
            await set_feed_update_state(req)

    async def test_opening_feed_update_form_seeds_it_from_settings(self):
        await PoddlaSettings.objects.aupdate_or_create(
            pk=1, defaults={"min_feed_update_freq": datetime.timedelta(hours=2, minutes=15)}
        )

        response = await self._post_feed_update({"show_form": True})

        self.assertEqual(response.status_code, 204)
        state = await self._get_feed_update_state()
        self.assertTrue(state.show_form)
        self.assertEqual(state.data, {"hours": 2, "minutes": 15})
        self.assertTrue(state.can_save)

    async def test_invalid_feed_update_data_cannot_be_saved(self):
        await self._post_feed_update({"show_form": True})

        response = await self._post_feed_update(
            {"show_form": True, "data": {"hours": -1, "minutes": 0}, "save": True}
        )

        self.assertEqual(response.status_code, 204)
        state = await self._get_feed_update_state()
        self.assertFalse(state.can_save)
        settings = await PoddlaSettings.objects.aget_settings()
        self.assertNotEqual(settings.min_feed_update_freq, datetime.timedelta(hours=-1))

    async def test_valid_feed_update_data_without_save_does_not_write(self):
        # Opening the form and editing its data are separate requests in real
        # usage (an "Edit" click, then typing) - the first seeds state.data
        # from the DB, so it has to happen before posting the edit itself.
        await self._post_feed_update({"show_form": True})

        response = await self._post_feed_update(
            {"show_form": True, "data": {"hours": 1, "minutes": 30}}
        )

        self.assertEqual(response.status_code, 204)
        state = await self._get_feed_update_state()
        self.assertTrue(state.can_save)
        self.assertTrue(state.show_form)
        settings = await PoddlaSettings.objects.aget_settings()
        self.assertNotEqual(settings.min_feed_update_freq, datetime.timedelta(hours=1, minutes=30))

    async def test_saving_feed_update_writes_settings_and_closes_form(self):
        await self._post_feed_update({"show_form": True})

        response = await self._post_feed_update(
            {"show_form": True, "data": {"hours": 1, "minutes": 30}, "save": True}
        )

        self.assertEqual(response.status_code, 204)
        settings = await PoddlaSettings.objects.aget_settings()
        self.assertEqual(settings.min_feed_update_freq, datetime.timedelta(hours=1, minutes=30))
        state = await self._get_feed_update_state()
        self.assertFalse(state.show_form)
        self.assertIsNone(state.data)
        self.assertFalse(state.can_save)

    async def test_disabling_media_files_expiry_saves_without_valid_weeks_days(self):
        await self._post_media_files({"show_form": True})

        response = await self._post_media_files(
            {"show_form": True, "enabled": False, "data": {}, "save": True}
        )

        self.assertEqual(response.status_code, 204)
        settings = await PoddlaSettings.objects.aget_settings()
        self.assertFalse(settings.media_files_expiry_enabled)
        state = await self._get_media_files_state()
        self.assertFalse(state.show_form)
        self.assertIsNone(state.data)

    async def test_saving_media_files_expiry_writes_settings_and_closes_form(self):
        await self._post_media_files({"show_form": True, "enabled": True})

        response = await self._post_media_files(
            {
                "show_form": True,
                "enabled": True,
                "data": {"weeks": 3, "days": 2},
                "save": True,
            }
        )

        self.assertEqual(response.status_code, 204)
        settings = await PoddlaSettings.objects.aget_settings()
        self.assertTrue(settings.media_files_expiry_enabled)
        self.assertEqual(settings.media_files_expire, datetime.timedelta(weeks=3, days=2))
        state = await self._get_media_files_state()
        self.assertFalse(state.show_form)
        self.assertIsNone(state.data)
        self.assertFalse(state.can_save)
