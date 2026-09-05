import base64
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from podcasts.models import Episode, PodcastFeed


def _basic_auth_headers(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class PodcastFeedRssTests(TestCase):
    def setUp(self):
        self.podcast = PodcastFeed.everything.create(
            status=PodcastFeed.STATUS_PUBLIC,
            name="Test podcast",
            url="https://youtube.com/@test",
            source_type="channel",
        )
        # A freshly created podcast isn't stale yet (needs_updating() is False), and the
        # view only bothers refreshing a stale one - make it look old enough to refresh.
        # Must exceed PoddlaSettings' default min_feed_update_freq (30 minutes):
        PodcastFeed.everything.filter(pk=self.podcast.pk).update(
            updated_at=timezone.now() - timedelta(minutes=45)
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            youtube_id="abc123",
            title="Episode one",
            url="https://youtube.com/watch?v=abc123",
            show_notes="",
        )
        self.user = User.objects.create_user(username="listener", password="not-the-basic-auth-one")
        self.user_settings = self.user.user_settings
        self.user_settings.basic_auth_password = "test-password"
        self.user_settings.save()
        self.client = Client()
        self.url = reverse("podcast_feed_rss", args=[self.podcast.pk])
        self.auth_headers = _basic_auth_headers("listener", "test-password")

    def test_requires_basic_auth(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["WWW-Authenticate"].startswith("Basic"))

    def test_rejects_wrong_basic_auth_password(self):
        response = self.client.get(self.url, headers=_basic_auth_headers("listener", "wrong"))

        self.assertEqual(response.status_code, 401)

    def test_fetching_a_stale_feed_triggers_a_refresh(self):
        mock_refresh = AsyncMock()
        with patch("podcasts.feeds.refresh_podcast_feed_task", mock_refresh):
            response = self.client.get(self.url, headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        mock_refresh.assert_awaited_once_with(podcast=self.podcast)

    def test_a_fresh_feed_is_not_refreshed_again(self):
        PodcastFeed.everything.filter(pk=self.podcast.pk).update(updated_at=timezone.now())
        mock_refresh = AsyncMock()
        with patch("podcasts.feeds.refresh_podcast_feed_task", mock_refresh):
            response = self.client.get(self.url, headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        mock_refresh.assert_not_awaited()

    def test_a_failed_refresh_propagates(self):
        mock_refresh = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("podcasts.feeds.refresh_podcast_feed_task", mock_refresh):
            with self.assertRaises(RuntimeError):
                self.client.get(self.url, headers=self.auth_headers)

    def test_feed_is_psp1_compliant(self):
        mock_refresh = AsyncMock()
        with patch("podcasts.feeds.refresh_podcast_feed_task", mock_refresh):
            response = self.client.get(self.url, headers=self.auth_headers)

        content = response.content.decode()
        self.assertIn('xmlns:atom="http://www.w3.org/2005/Atom"', content)
        self.assertNotIn("http://www.w3.org/2005/Atom/", content)
        self.assertIn('rel="self"', content)
        self.assertIn('type="application/rss+xml"', content)
        self.assertIn("<itunes:category", content)
        self.assertIn("<itunes:explicit>false</itunes:explicit>", content)
        # No downloaded file for this episode yet, so the enclosure length falls back to 0.
        self.assertIn('length="0"', content)

    def test_enclosure_url_includes_the_users_basic_auth_credentials(self):
        mock_refresh = AsyncMock()
        with patch("podcasts.feeds.refresh_podcast_feed_task", mock_refresh):
            response = self.client.get(self.url, headers=self.auth_headers)

        content = response.content.decode()
        self.assertIn('url="http://listener:test-password@testserver/e/', content)

    def test_enclosure_length_reflects_a_downloaded_file(self):
        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)
        episode_path = media_root / "abc123.m4a"
        episode_path.write_bytes(b"x" * 1234)
        self.addCleanup(episode_path.unlink)
        self.episode.file_path = episode_path.name
        self.episode.save()

        mock_refresh = AsyncMock()
        with patch("podcasts.feeds.refresh_podcast_feed_task", mock_refresh):
            response = self.client.get(self.url, headers=self.auth_headers)

        self.assertIn('length="1234"', response.content.decode())

    def test_content_encoded_escapes_literal_cdata_close_sequence(self):
        self.episode.show_notes = "Look at this: ]]>"
        self.episode.save()

        mock_refresh = AsyncMock()
        with patch("podcasts.feeds.refresh_podcast_feed_task", mock_refresh):
            response = self.client.get(self.url, headers=self.auth_headers)

        content = response.content.decode()
        self.assertIn("<content:encoded><![CDATA[Look at this: ]]]]><![CDATA[>]]></content:encoded>", content)
