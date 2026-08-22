import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import AsyncRequestFactory, TransactionTestCase, override_settings

from podcasts.models import Episode, PodcastFeed, podcast_publisher
from podcasts.podcast_feed_views import episode_media
from podcasts.youtube.video import DownloadInfo
from youtube_to_podcast import valkey_client


def make_mock_download(*, delay: float = 0, call_counter: dict | None = None):
    def _download(url: str, base_path: Path, file_path: Path) -> DownloadInfo:
        if call_counter is not None:
            call_counter["n"] += 1
        if delay:
            time.sleep(delay)
        actual = (base_path / file_path).with_suffix(".mp3")
        actual.parent.mkdir(parents=True, exist_ok=True)
        actual.write_bytes(b"fake audio")
        return DownloadInfo(file_path=actual)

    return _download


async def _flush_valkey():
    vk = await valkey_client.get_client()
    await vk.flushdb()


@override_settings(VALKEY_DB=15)
class EpisodeMediaTests(TransactionTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.media_root = Path(self._tmp.name)
        self.podcast = PodcastFeed.everything.create(
            name="Test", url="https://youtube.com/@test", source_type="channel"
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            youtube_id="abc123",
            title="Test video title",
            url="https://youtube.com/abc123",
            show_notes="",
        )
        self.factory = AsyncRequestFactory()
        async_to_sync(_flush_valkey)()

    def tearDown(self):
        async_to_sync(_flush_valkey)()
        self._tmp.cleanup()

    async def test_single_request_downloads_and_serves(self):
        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        with patch("podcasts.podcast_feed_views.download_audio", make_mock_download()):
            with override_settings(MEDIA_ROOT=str(self.media_root)):
                response = await episode_media(req, self.episode.pk)

        self.assertIn(response.status_code, (200, 206))
        await self.episode.arefresh_from_db()
        self.assertIsNotNone(self.episode.file_path)

    async def test_concurrent_requests_download_once(self):
        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        counter: dict = {"n": 0}
        with patch(
            "podcasts.podcast_feed_views.download_audio",
            make_mock_download(delay=0.5, call_counter=counter),
        ):
            with override_settings(MEDIA_ROOT=str(self.media_root)):
                r1, r2, r3 = await asyncio.gather(
                    episode_media(req, self.episode.pk),
                    episode_media(req, self.episode.pk),
                    episode_media(req, self.episode.pk),
                )

        self.assertEqual(counter["n"], 1)
        self.assertIn(r1.status_code, (200, 206))
        self.assertIn(r2.status_code, (200, 206))
        self.assertIn(r3.status_code, (200, 206))

    async def test_already_downloaded_skips_download(self):
        path = self.media_root / str(self.episode.podcast_id) / f"{self.episode.id}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing audio")
        self.episode.file_path = str(path.relative_to(self.media_root))
        await self.episode.asave(update_fields=["file_path"])

        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        counter: dict = {"n": 0}
        with patch(
            "podcasts.podcast_feed_views.download_audio",
            make_mock_download(call_counter=counter),
        ):
            with override_settings(MEDIA_ROOT=str(self.media_root)):
                response = await episode_media(req, self.episode.pk)

        self.assertEqual(counter["n"], 0)
        self.assertIn(response.status_code, (200, 206))

    async def test_download_completes_after_client_disconnect(self):
        req = self.factory.get(f"/e/{self.episode.pk}/media/")

        done = asyncio.Event()
        channel = podcast_publisher.channel_for(self.episode.pk)
        subscriber = await valkey_client.create_subscriber(
            channel, callback=lambda msg, ctx: ctx.set(), context=done
        )
        try:
            with patch("podcasts.podcast_feed_views.download_audio", make_mock_download(delay=0.3)):
                with override_settings(MEDIA_ROOT=str(self.media_root)):
                    task = asyncio.create_task(episode_media(req, self.episode.pk))
                    await asyncio.sleep(0.05)  # let it acquire the lock and start the background task
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                    # _download_and_save is still running; wait for asave() to fire the pub/sub signal
                    await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            await subscriber.close()

        await self.episode.arefresh_from_db()
        self.assertIsNotNone(self.episode.file_path)
