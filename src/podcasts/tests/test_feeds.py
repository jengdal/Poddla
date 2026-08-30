from datetime import timedelta
from unittest.mock import AsyncMock, patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from podcasts.models import Episode, PodcastFeed


class PodcastFeedRssTests(TestCase):
    def setUp(self):
        self.podcast = PodcastFeed.everything.create(
            status=PodcastFeed.STATUS_PUBLIC,
            name="Test podcast",
            url="https://youtube.com/@test",
            source_type="channel",
        )
        # A freshly created podcast isn't stale yet (needs_updating() is False), and the
        # view only bothers refreshing a stale one - make it look old enough to refresh:
        PodcastFeed.everything.filter(pk=self.podcast.pk).update(
            updated_at=timezone.now() - timedelta(minutes=10)
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            youtube_id="abc123",
            title="Episode one",
            url="https://youtube.com/watch?v=abc123",
            show_notes="",
        )
        self.client = Client()
        self.url = reverse("podcast_feed_rss", args=[self.podcast.pk])

    def test_fetching_a_stale_feed_triggers_a_refresh(self):
        mock_refresh = AsyncMock()
        with patch("podcasts.feeds.refresh_podcast_feed", mock_refresh):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        mock_refresh.assert_awaited_once_with(podcast=self.podcast)

    def test_a_fresh_feed_is_not_refreshed_again(self):
        PodcastFeed.everything.filter(pk=self.podcast.pk).update(updated_at=timezone.now())
        mock_refresh = AsyncMock()
        with patch("podcasts.feeds.refresh_podcast_feed", mock_refresh):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        mock_refresh.assert_not_awaited()

    def test_a_failed_refresh_still_serves_the_existing_feed(self):
        mock_refresh = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("podcasts.feeds.refresh_podcast_feed", mock_refresh):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.episode.title.encode(), response.content)
