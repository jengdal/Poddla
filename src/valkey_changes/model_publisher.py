from glide import Batch

from valkey_changes import changes, valkey_client


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

    # TODO: let the caller pass in a function that resolves the pk of the "main" model. This lets child model changes publish on their parents pk.
    def register(self, *model_classes) -> None:
        """Connect post_save and post_delete signals for each model class.

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
            # Django clears instance.pk on deletes, after the transaction commits. Keep track
            # of it here:
            pk = instance.pk
            transaction.on_commit(lambda: async_to_sync(self.publish)(pk=pk), robust=True)

        uid = f"model_publisher:{self._channel_name}"
        for model in model_classes:
            post_save.connect(handler, sender=model, weak=False, dispatch_uid=uid)
            post_delete.connect(handler, sender=model, weak=False, dispatch_uid=uid)
