import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from podcasts.downloader import _refresh_podcast_feed, refresh_podcast_feed
from podcasts.models import Episode, PodcastFeed
from podcasts.youtube.feed import FeedSource, VideoInfo
from valkey_changes import valkey_client


async def _flush_valkey():
    vk = await valkey_client.get_client()
    await vk.flushdb()


def make_feed(videos: list[VideoInfo]) -> FeedSource:
    return FeedSource(
        url="https://youtube.com/@test",
        title="A test channel",
        source_type="channel",
        videos=videos,
    )


def patch_fetch_feed(feed: FeedSource):
    return patch("podcasts.downloader.fetch_feed", AsyncMock(return_value=feed))


@override_settings(VALKEY_DB=15)
class RefreshPodcastFeedTests(TransactionTestCase):
    def setUp(self):
        async_to_sync(_flush_valkey)()

    def tearDown(self):
        async_to_sync(_flush_valkey)()

    async def make_podcast(self, **kwargs) -> PodcastFeed:
        kwargs.setdefault("status", PodcastFeed.STATUS_PUBLIC)
        kwargs.setdefault("source_type", "channel")
        kwargs.setdefault("url", "https://youtube.com/@test")
        kwargs.setdefault("name", "Old name")
        return await PodcastFeed.everything.acreate(**kwargs)

    async def test_creates_new_episodes_from_feed(self):
        podcast = await self.make_podcast()
        feed = make_feed(
            [
                VideoInfo(
                    id="v1",
                    title="Video one",
                    url="https://youtube.com/watch?v=v1",
                    duration=100,
                    thumbnail="https://example.com/v1.jpg",
                    timestamp=1_700_000_000,
                )
            ]
        )

        with patch_fetch_feed(feed):
            await _refresh_podcast_feed(podcast=podcast)

        episode = await Episode.objects.aget(podcast=podcast, url="https://youtube.com/watch?v=v1")
        self.assertEqual(episode.title, "Video one")
        self.assertEqual(episode.duration, 100)
        self.assertEqual(episode.thumbnail, "https://example.com/v1.jpg")
        self.assertIsNotNone(episode.published_at)

    async def test_existing_episode_title_updates_without_clobbering_download_fields(self):
        podcast = await self.make_podcast()
        episode = await Episode.objects.acreate(
            podcast=podcast,
            youtube_id="v1",
            title="Old title",
            url="https://youtube.com/watch?v=v1",
            duration=123,
            show_notes="Real show notes from the downloaded file",
            file_path="1/1.mp3",
        )
        # The feed's own metadata is much worse than what a download already produced:
        feed = make_feed(
            [
                VideoInfo(
                    id="v1",
                    title="New title from the feed",
                    url="https://youtube.com/watch?v=v1",
                    duration=999,
                    thumbnail="https://example.com/different.jpg",
                    timestamp=1_700_000_000,
                )
            ]
        )

        with patch_fetch_feed(feed):
            await _refresh_podcast_feed(podcast=podcast)

        await episode.arefresh_from_db()
        self.assertEqual(episode.title, "New title from the feed")
        # Everything _download_and_update owns must survive the feed refresh untouched:
        self.assertEqual(episode.duration, 123)
        self.assertEqual(episode.show_notes, "Real show notes from the downloaded file")
        self.assertEqual(episode.file_path, "1/1.mp3")

    async def test_episodes_are_scoped_to_their_own_podcast(self):
        podcast_a = await self.make_podcast(url="https://youtube.com/@a")
        podcast_b = await self.make_podcast(url="https://youtube.com/@b")
        shared_url = "https://youtube.com/watch?v=shared"
        b_episode = await Episode.objects.acreate(
            podcast=podcast_b,
            youtube_id="shared",
            title="Podcast B's episode",
            url=shared_url,
            show_notes="",
        )
        feed = make_feed(
            [
                VideoInfo(
                    id="shared",
                    title="Podcast A's view of the same video",
                    url=shared_url,
                    duration=10,
                    timestamp=None,
                )
            ]
        )

        with patch_fetch_feed(feed):
            await _refresh_podcast_feed(podcast=podcast_a)

        # Podcast A gets its own episode for the shared URL...
        a_episode = await Episode.objects.aget(podcast=podcast_a, url=shared_url)
        self.assertEqual(a_episode.title, "Podcast A's view of the same video")
        # ...and podcast B's row is untouched, not repurposed for A:
        await b_episode.arefresh_from_db()
        self.assertEqual(b_episode.title, "Podcast B's episode")
        self.assertNotEqual(a_episode.pk, b_episode.pk)

    async def test_refresh_skips_when_not_needed(self):
        podcast = await self.make_podcast()  # updated_at is "now", so needs_updating() is False
        mock = AsyncMock(return_value=make_feed([]))

        with patch("podcasts.downloader.fetch_feed", mock):
            await refresh_podcast_feed(podcast=podcast)

        mock.assert_not_awaited()

    async def test_concurrent_refresh_calls_dedupe_to_a_single_fetch(self):
        podcast = await self.make_podcast()
        # Must exceed PoddlaSettings' default min_feed_update_freq (30 minutes) for
        # needs_updating() to consider this podcast stale enough to refresh:
        await PodcastFeed.everything.filter(pk=podcast.pk).aupdate(
            updated_at=timezone.now() - timedelta(minutes=45)
        )
        # An episode already matching the feed's one video means `_fetch_and_update`
        # finds an "old" episode on its first pass, so a single refresh only calls
        # `fetch_feed` once (no need to page for older entries) - isolating this
        # test to the lock/dedup behavior instead of the pagination logic.
        await Episode.objects.acreate(
            podcast=podcast,
            youtube_id="v1",
            title="Video one",
            url="https://youtube.com/watch?v=v1",
            show_notes="",
        )
        feed = make_feed(
            [
                VideoInfo(
                    id="v1",
                    title="Video one",
                    url="https://youtube.com/watch?v=v1",
                    duration=100,
                    timestamp=1_700_000_000,
                )
            ]
        )

        async def slow_fetch(**kwargs):
            await asyncio.sleep(0.2)
            return feed

        mock = AsyncMock(side_effect=slow_fetch)

        with patch("podcasts.downloader.fetch_feed", mock):
            await asyncio.gather(
                refresh_podcast_feed(podcast=podcast),
                refresh_podcast_feed(podcast=podcast),
            )

        self.assertEqual(mock.await_count, 1)

    async def test_an_empty_feed_does_not_trigger_pagination_for_more(self):
        podcast = await self.make_podcast()
        mock = AsyncMock(return_value=make_feed([]))

        with patch("podcasts.downloader.fetch_feed", mock):
            await _refresh_podcast_feed(podcast=podcast)

        mock.assert_awaited_once()
