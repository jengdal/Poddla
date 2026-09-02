import asyncio
from unittest.mock import patch

import msgspec
from asgiref.sync import async_to_sync
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, override_settings

from valkey_changes import valkey_client
from valkey_changes.changes import _registries, changes
from valkey_changes.model_publisher import ModelPublisher
from valkey_changes.state_store import StateStore

TIMEOUT = 5


class _State(msgspec.Struct):
    tab_id: str
    value: str = ""


_store: StateStore[_State] = StateStore(
    _State,
    namespace="test_state_store",
    default_factory=lambda tab_id: _State(tab_id=tab_id),
)
_publisher = ModelPublisher("test_state_store:models")


async def _flush_valkey():
    vk = await valkey_client.get_client()
    await vk.flushdb()


@override_settings(VALKEY_DB=15)
class StateStoreTests(SimpleTestCase):
    def setUp(self):
        self.user_id = 1
        self.tab_id = f"{self.user_id}:tab-under-test"
        # Each async test runs on its own loop and so gets its own client; fetch it in the test.
        async_to_sync(_flush_valkey)()

    def tearDown(self):
        async_to_sync(_flush_valkey)()

    async def test_state_is_read_when_changes_starts(self):
        await _store.save(
            self.tab_id, _State(tab_id=self.tab_id, value="stored"), self.user_id
        )

        tab = _store.subscribe(self.tab_id, self.user_id)
        async with changes(tab):
            self.assertEqual(tab.state.value, "stored")

    async def test_state_is_the_default_when_nothing_was_saved(self):
        tab = _store.subscribe(self.tab_id, self.user_id)
        async with changes(tab):
            self.assertEqual(tab.state, _State(tab_id=self.tab_id))

    async def test_state_is_unreadable_outside_changes(self):
        tab = _store.subscribe(self.tab_id, self.user_id)
        with self.assertRaises(RuntimeError):
            tab.state  # noqa: B018

    async def test_a_save_arrives_without_a_read(self):
        # The whole point of putting the payload on the message: a change must not cost a round
        # trip. Making get() explode proves the new state came off the message and nowhere else.
        tab = _store.subscribe(self.tab_id, self.user_id)
        async with changes(tab) as changed:
            with patch.object(StateStore, "get", side_effect=AssertionError("read the state")):
                await _store.save(
                    self.tab_id,
                    _State(tab_id=self.tab_id, value="from the message"),
                    self.user_id,
                )
                async with asyncio.timeout(TIMEOUT):
                    await changed.wait()

            self.assertEqual(tab.state.value, "from the message")

    async def test_a_wake_without_a_payload_falls_back_to_a_read(self):
        vk = await valkey_client.get_client()
        # The pump wakes everyone when it can no longer say what changed. A subscriber has to go
        # and look rather than carry on with a state it can no longer trust.
        tab = _store.subscribe(self.tab_id, self.user_id)
        async with changes(tab) as changed:
            # Write behind the store's back, so the only way to see it is an actual read:
            await vk.set(
                f"test_state_store:state:{self.tab_id}",
                msgspec.msgpack.encode(_State(tab_id=self.tab_id, value="written directly")),
            )
            _registries[asyncio.get_running_loop()]._wake_everyone()

            async with asyncio.timeout(TIMEOUT):
                await changed.wait()
            self.assertEqual(tab.state.value, "written directly")

    async def test_overlapping_saves_leave_the_state_agreeing_with_valkey(self):
        # save() writes and publishes as one atomic step. Were it two, saves could interleave as
        # SET(a), SET(b), PUBLISH(a) and strand a subscriber on a payload that isn't what's stored.
        tab = _store.subscribe(self.tab_id, self.user_id)
        async with changes(tab) as changed:
            await asyncio.gather(
                *[
                    _store.save(
                        self.tab_id, _State(tab_id=self.tab_id, value=f"{n}"), self.user_id
                    )
                    for n in range(20)
                ]
            )
            # Drain until it goes quiet, so we compare the last thing delivered.
            while True:
                try:
                    async with asyncio.timeout(0.5):
                        await changed.wait()
                except TimeoutError:
                    break

            stored = await _store.get(self.tab_id, self.user_id)
            self.assertEqual(tab.state, stored)

    async def test_wait_reports_which_source_fired(self):
        tab = _store.subscribe(self.tab_id, self.user_id)
        models = _publisher.subscribe()
        async with changes(tab, models) as changed:
            await _publisher.publish()

            async with asyncio.timeout(TIMEOUT):
                fired = await changed.wait()
            self.assertEqual(fired, (models,))

            await _store.save(
                self.tab_id, _State(tab_id=self.tab_id, value="mine"), self.user_id
            )
            async with asyncio.timeout(TIMEOUT):
                fired = await changed.wait()
            self.assertEqual(fired, (tab,))

    async def test_new_scopes_the_tab_id_to_the_user_and_persists_it(self):
        state = await _store.new(self.user_id)
        self.assertTrue(state.tab_id.startswith(f"{self.user_id}:"))

        stored = await _store.get(state.tab_id, self.user_id)
        self.assertEqual(stored, state)

    async def test_new_scopes_anonymous_tabs_to_user_id_zero(self):
        state = await _store.new(None)
        self.assertTrue(state.tab_id.startswith("0:"))

    async def test_get_rejects_a_tab_id_owned_by_someone_else(self):
        with self.assertRaises(PermissionDenied):
            await _store.get(self.tab_id, self.user_id + 1)

    async def test_save_rejects_a_tab_id_owned_by_someone_else(self):
        with self.assertRaises(PermissionDenied):
            await _store.save(
                self.tab_id, _State(tab_id=self.tab_id, value="hijacked"), self.user_id + 1
            )

    async def test_subscribe_rejects_a_tab_id_owned_by_someone_else(self):
        with self.assertRaises(PermissionDenied):
            _store.subscribe(self.tab_id, self.user_id + 1)
