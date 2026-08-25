from glide import GlideClient


class ModelPublisher:
    def __init__(self, channel_name: str) -> None:
        self._channel_name = channel_name

    @property
    def channel(self) -> str:
        """Subscribe here to receive all changes to this model type."""
        return self._channel_name

    def channel_for(self, pk) -> str:
        """Subscribe here to receive changes to a single model instance."""
        return f"{self._channel_name}:{pk}"

    async def publish(self, vk: GlideClient, pk=None) -> None:
        await vk.publish(b"1", self._channel_name)
        if pk is not None:
            await vk.publish(b"1", self.channel_for(pk))

    def register(self, *model_classes) -> None:
        """Connect post_save and post_delete signals for each model class.

        NOTE: QuerySet.update() bypasses Django signals and will NOT trigger
        pub/sub. For bulk updates, call publisher.publish(vk) explicitly.
        """
        from asgiref.sync import async_to_sync
        from django.db.models.signals import post_delete, post_save

        def handler(sender, instance, **kwargs):
            async_to_sync(self._publish_async)(pk=instance.pk)

        for model in model_classes:
            post_save.connect(handler, sender=model, weak=False)
            post_delete.connect(handler, sender=model, weak=False)

    async def _publish_async(self, pk=None) -> None:
        from poddla import valkey_client

        vk = await valkey_client.get_client()
        await self.publish(vk, pk=pk)
