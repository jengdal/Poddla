import datetime

from django.db import models

from valkey_changes.model_publisher import ModelPublisher

settings_publisher = ModelPublisher("settings:updates")


class PoddlaSettingsManager(models.Manager):
    def get_settings(self) -> "PoddlaSettings":  # noqa: UP037
        settings, _ = self.get_or_create(pk=1)
        return settings

    async def aget_settings(self) -> "PoddlaSettings":  # noqa: UP037
        settings, _ = await self.aget_or_create(pk=1)
        return settings


class PoddlaSettings(models.Model):
    objects = PoddlaSettingsManager()

    # Update the feeds from the source at most this frequently.
    min_feed_update_freq = models.DurationField(default=datetime.timedelta(minutes=30))

    # Delete files older than this.
    media_files_expire = models.DurationField(default=datetime.timedelta(weeks=2))
    media_files_expiry_enabled = models.BooleanField(default=True)

    class Meta:
        # There can be only one row in the db table:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="poddla_settings_singleton"),
        ]


settings_publisher.register(PoddlaSettings)
