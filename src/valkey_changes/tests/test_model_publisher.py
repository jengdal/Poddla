import asyncio

from asgiref.sync import async_to_sync, sync_to_async
from django.db import transaction
from django.db.models.signals import post_save
from django.test import TransactionTestCase, override_settings
from glide import ClosingError

from valkey_changes import valkey_client
from valkey_changes.changes import Changes, Source, changes
from valkey_changes.tests.models import Part, Thing, publisher

TIMEOUT = 5


async def _flush_valkey():
    vk = await valkey_client.get_client()
    await vk.flushdb()


async def _wait_for_all(changed: Changes, *sources: Source, timeout=TIMEOUT) -> None:
    """Wait until every one of the sources has fired at least once.

    A save publishes on the type channel and the instance channel separately, so the two arrive as
    two messages and a single wait() may only report one of them.
    """
    remaining = set(sources)
    async with asyncio.timeout(timeout):
        while remaining:
            remaining.difference_update(await changed.wait())


def _delete_in_transaction(instance) -> None:
    with transaction.atomic():
        instance.delete()


_created_clients = []


def _publish_from_a_sync_thread(pk) -> None:
    """Publish the way a signal handler does when it fires from sync code."""

    async def _publish():
        vk = await valkey_client.get_client()
        _created_clients.append(vk)
        await publisher.publish(pk=pk)

    # There is no event loop to hand this back to in this thread, so asgiref runs it on one of
    # its own making:
    async_to_sync(_publish)()


@override_settings(VALKEY_DB=15)
class ModelPublisherTests(TransactionTestCase):
    def setUp(self):
        self.thing = Thing.objects.create(name="Test thing")
        async_to_sync(_flush_valkey)()

    def tearDown(self):
        async_to_sync(_flush_valkey)()

    async def test_save_publishes_on_the_type_and_instance_channels(self):
        type_updates = publisher.subscribe()
        instance_updates = publisher.subscribe(pk=self.thing.pk)
        async with changes(type_updates, instance_updates) as changed:
            self.thing.name = "A new name"
            await self.thing.asave(update_fields=["name"])

            await _wait_for_all(changed, type_updates, instance_updates)

    async def test_delete_publishes_on_the_instance_channel(self):
        instance_updates = publisher.subscribe(pk=self.thing.pk)
        async with changes(instance_updates) as changed:
            await self.thing.adelete()

            await _wait_for_all(changed, instance_updates)

    async def test_delete_in_a_transaction_publishes_on_the_instance_channel(self):
        # Django clears instance.pk after the outermost atomic block exits, which is after our
        # on_commit callback has run. The publisher has to have kept the pk from when the signal
        # fired, or listeners waiting on this channel never hear about the delete. Any caller
        # that deletes a row inside atomic() and expects watchers to notice depends on this.
        instance_updates = publisher.subscribe(pk=self.thing.pk)
        async with changes(instance_updates) as changed:
            await sync_to_async(_delete_in_transaction)(self.thing)

            await _wait_for_all(changed, instance_updates)

        # Sanity check that we exercised the path described above:
        self.assertIsNone(self.thing.pk)

    async def test_raw_saves_do_not_publish(self):
        # This is what loaddata does. Nobody is watching a database that is being seeded.
        type_updates = publisher.subscribe()
        instance_updates = publisher.subscribe(pk=self.thing.pk)
        async with changes(type_updates, instance_updates) as changed:
            await sync_to_async(post_save.send)(
                sender=Thing,
                instance=self.thing,
                created=False,
                raw=True,
                using="default",
            )

            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(changed.wait(), timeout=0.5)

    async def test_a_sync_publish_closes_the_client_it_created(self):
        # Signal handlers publish through async_to_sync, which spins up an event loop of its own
        # when it isn't nested inside sync_to_async. The client that loop creates has to be closed
        # again when the loop goes away: glide keeps a reference to every client it creates until
        # close() is called, so one we just drop stays connected for the rest of the process.
        instance_updates = publisher.subscribe(pk=self.thing.pk)

        async with changes(instance_updates) as changed:
            await asyncio.to_thread(_publish_from_a_sync_thread, self.thing.pk)

            await _wait_for_all(changed, instance_updates)

        # wait_for so that a client that was left open fails the test instead of hanging on its
        # dead event loop:
        with self.assertRaises(ClosingError):
            await asyncio.wait_for(_created_clients[0].ping(), timeout=2)


@override_settings(VALKEY_DB=15)
class ResolvePkTests(TransactionTestCase):
    """Part is registered with resolve_pk=lambda instance: instance.thing_id, so its changes
    should publish on its parent Thing's pk, never on its own.
    """

    def setUp(self):
        # A throwaway row so Thing's and Part's autoincrement pks can't coincidentally collide,
        # which would make test_child_save_does_not_publish_on_its_own_pk_channel pass by accident.
        Thing.objects.create(name="Padding")
        self.thing = Thing.objects.create(name="Test thing")
        self.part = Part.objects.create(thing=self.thing)
        async_to_sync(_flush_valkey)()

    def tearDown(self):
        async_to_sync(_flush_valkey)()

    async def test_child_save_publishes_on_the_type_and_parent_pk_channels(self):
        type_updates = publisher.subscribe()
        parent_updates = publisher.subscribe(pk=self.thing.pk)
        async with changes(type_updates, parent_updates) as changed:
            await self.part.asave()

            await _wait_for_all(changed, type_updates, parent_updates)

    async def test_child_delete_publishes_on_the_parent_pk_channel(self):
        parent_updates = publisher.subscribe(pk=self.thing.pk)
        async with changes(parent_updates) as changed:
            await self.part.adelete()

            await _wait_for_all(changed, parent_updates)

    async def test_child_save_does_not_publish_on_its_own_pk_channel(self):
        # resolve_pk's result replaces instance.pk entirely, it isn't published in addition to it.
        own_updates = publisher.subscribe(pk=self.part.pk)
        async with changes(own_updates) as changed:
            await self.part.asave()

            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(changed.wait(), timeout=0.5)

    async def test_child_delete_in_a_transaction_still_resolves_the_parent_pk(self):
        # Mirrors test_delete_in_a_transaction_publishes_on_the_instance_channel: resolve_pk has
        # to run from the signal handler itself, before the transaction commits, or a delete
        # inside atomic() would have nothing left to resolve a pk from.
        parent_updates = publisher.subscribe(pk=self.thing.pk)
        async with changes(parent_updates) as changed:
            await sync_to_async(_delete_in_transaction)(self.part)

            await _wait_for_all(changed, parent_updates)
