from collections.abc import Callable
from typing import TYPE_CHECKING

from glide import Batch

from valkey_changes import changes, valkey_client

if TYPE_CHECKING:
    from django.db.models import Model


class ModelPublisher:
    def __init__(self, channel_name: str) -> None:
        self._channel_name = channel_name

    def channel_for(self, pk: object) -> str:
        """Subscribe here to receive changes to a single model instance."""
        return f"{self._channel_name}:{pk}"

    def subscribe(self, pk: object = None) -> changes.Source:
        """A source for changes() that wakes on any change to this model type.

        Pass a pk to wake only on changes to that one instance.
        """
        channel = self._channel_name
        if pk is not None:
            channel = self.channel_for(pk)
        return changes.Source(channel)

    async def publish(self, pk: object = None) -> None:
        vk = await valkey_client.get_client()
        if pk is None:
            await vk.publish(b"1", self._channel_name)
            return

        # No point in doing this atomically, it's two publishes either way.
        batch = Batch(is_atomic=False)
        batch.publish(b"1", self._channel_name)
        batch.publish(b"1", self.channel_for(pk))
        await vk.exec(batch, raise_on_error=True)

    def register(self, model_class, resolve_pk: Callable[["Model"], object] | None = None) -> None:
        """Connect post_save and post_delete signals for a model class.

        If your ModelPublisher instance is meant for a model Author, but you
        also want the related model Book to trigger a change on its author when
        it changes, you can set that up with:

        ```python
        class Author(models.Model):
            pass
        class Book(models.Model):
            author = models.ForeignKey(Author, related_name="books")

        author_publisher = ModelPublisher('author')
        author_publisher.register(Author)
        author_publisher.register(Book, resolve_pk=lambda instance: instance.author_id)
        ```

        In this setup, when a Book instance is saved or deleted
        author_publisher will notify subscribers that
        1. A author has changed.
        2. The author with pk `book.author_id` has changed.

        NOTE: QuerySet.update() bypasses Django signals and will NOT trigger
        pub/sub. For bulk updates, call publisher.publish() explicitly.
        """
        from asgiref.sync import async_to_sync
        from django.db import transaction
        from django.db.models.signals import post_delete, post_save

        def handler(sender, instance, raw=False, **kwargs):
            if raw:
                # Loaddata etc.
                return
            if resolve_pk:
                pk = resolve_pk(instance)
            else:
                pk = instance.pk
            transaction.on_commit(lambda: async_to_sync(self.publish)(pk=pk), robust=True)

        uid = f"model_publisher:{self._channel_name}"
        post_save.connect(handler, sender=model_class, weak=False, dispatch_uid=uid)
        post_delete.connect(handler, sender=model_class, weak=False, dispatch_uid=uid)
