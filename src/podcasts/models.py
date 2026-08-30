from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

from valkey_changes.model_publisher import ModelPublisher

podcast_publisher = ModelPublisher("podcasts:feed:updates")
episode_publisher = ModelPublisher("episodes:feed:updates")
PODCAST_FEED_OLD_MINUTES = 5


class PublicPodcastManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(status="public")


class DraftPodcastManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(status="draft")


class PodcastFeed(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLIC = "public"
    STATUS_CHOICES = [(STATUS_DRAFT, "Draft"), (STATUS_PUBLIC, "Public")]

    SOURCE_CHANNEL = "channel"
    SOURCE_PLAYLIST = "playlist"
    SOURCE_CHOICES = [(SOURCE_CHANNEL, "Channel"), (SOURCE_PLAYLIST, "Playlist")]

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # TODO: Add a new `source` attribute if we're going to support hosts other than youtube
    source_type = models.CharField(max_length=16, choices=SOURCE_CHOICES, blank=True)
    url = models.URLField()
    name = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    description = models.TextField(blank=True)
    thumbnail = models.URLField(blank=True)
    # channel_id = models.CharField(max_length=255, blank=True)

    objects = PublicPodcastManager()
    drafts = DraftPodcastManager()
    everything = models.Manager()

    def needs_updating(self) -> bool:
        return self.updated_at + timedelta(minutes=PODCAST_FEED_OLD_MINUTES) <= timezone.now()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["url"],
                condition=models.Q(status="public"),
                name="unique_public_source_url",
            )
        ]


class Episode(models.Model):
    podcast = models.ForeignKey(PodcastFeed, on_delete=models.CASCADE, related_name="episodes")
    # TODO: Do we need youtube_id?
    youtube_id = models.CharField(max_length=255)
    title = models.TextField()
    url = models.URLField()
    duration = models.IntegerField(null=True, blank=True)
    thumbnail = models.URLField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    show_notes = models.TextField()

    file_path = models.CharField(max_length=100, null=True, blank=True)

    def file_exists(self) -> Path | None:
        media_root = Path(settings.MEDIA_ROOT)
        if self.file_path:
            episode_file = media_root / str(self.file_path)
        else:
            episode_file = None
        if episode_file is not None and episode_file.exists():
            return episode_file

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["podcast", "url"],
                name="unique_episode_per_podcast",
            )
        ]


podcast_publisher.register(PodcastFeed)
podcast_publisher.register(Episode, resolve_pk=lambda instance: instance.podcast_id)
episode_publisher.register(Episode)
