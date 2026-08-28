import asyncio
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import AsyncRequestFactory, TransactionTestCase, override_settings

from podcasts import downloader
from podcasts.models import Episode, EpisodeDownload, PodcastFeed, podcast_publisher
from podcasts.podcast_feed_views import episode_media
from podcasts.youtube.video import DownloadInfo
from poddla import valkey_client


@asynccontextmanager
async def running_downloader():
    """Starts the podcasts.downloader for the duration of a test."""
    downloader.start()
    try:
        yield
    finally:
        await downloader.stop()


def make_mock_download(
    *,
    delay: float = 0,
    call_counter: dict | None = None,
    error: Exception | None = None,
):
    def _download(url: str, base_path: Path, file_path: Path) -> DownloadInfo:
        if call_counter is not None:
            call_counter["n"] += 1
        if delay:
            time.sleep(delay)
        if error is not None:
            raise error
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
        async with running_downloader():
            with patch("podcasts.downloader.download_audio", make_mock_download()):
                with override_settings(MEDIA_ROOT=str(self.media_root)):
                    response = await episode_media(req, self.episode.pk)

        self.assertIn(response.status_code, (200, 206))
        await self.episode.arefresh_from_db()
        self.assertIsNotNone(self.episode.file_path)

    async def test_concurrent_requests_download_once(self):
        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        counter: dict = {"n": 0}
        async with running_downloader():
            with patch(
                "podcasts.downloader.download_audio",
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
        async with running_downloader():
            with patch(
                "podcasts.downloader.download_audio",
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
            async with running_downloader():
                with patch(
                    "podcasts.downloader.download_audio", make_mock_download(delay=0.3)
                ):
                    with override_settings(MEDIA_ROOT=str(self.media_root)):
                        task = asyncio.create_task(episode_media(req, self.episode.pk))
                        await asyncio.sleep(
                            0.05
                        )  # let it create the EpisodeDownload row and start waiting
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass

                        # The download is owned by the downloader singleton, not the cancelled
                        # request — wait for asave() to fire the pub/sub signal.
                        await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            await subscriber.close()

        await self.episode.arefresh_from_db()
        self.assertIsNotNone(self.episode.file_path)

    async def test_failed_download_is_retried_on_next_request(self):
        req = self.factory.get(f"/e/{self.episode.pk}/media/")

        # First attempt fails — the EpisodeDownload row should end up "failed".
        async with running_downloader():
            with patch(
                "podcasts.downloader.download_audio",
                make_mock_download(error=RuntimeError("boom")),
            ):
                with override_settings(MEDIA_ROOT=str(self.media_root)):
                    task = asyncio.create_task(episode_media(req, self.episode.pk))
                    for _ in range(50):
                        download = await EpisodeDownload.objects.filter(
                            episode=self.episode
                        ).afirst()
                        if (
                            download is not None
                            and download.status == EpisodeDownload.STATUS_FAILED
                        ):
                            break
                        await asyncio.sleep(0.1)
                    else:
                        self.fail("EpisodeDownload never reached status=failed")
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        self.assertEqual(download.status, EpisodeDownload.STATUS_FAILED)

        # Second attempt succeeds — episode_media should reset the row to pending
        # and the singleton should pick it up again.
        async with running_downloader():
            with (
                patch("podcasts.downloader.download_audio", make_mock_download()),
                override_settings(MEDIA_ROOT=str(self.media_root)),
            ):
                response = await episode_media(req, self.episode.pk)

            self.assertIn(response.status_code, (200, 206))
            await self.episode.arefresh_from_db()
            self.assertIsNotNone(self.episode.file_path)

            # episode_media returns as soon as the episode's file_path is saved, which
            # happens slightly before the downloader deletes the now-finished row —
            # give it a moment to finish that cleanup before the singleton is stopped.
            for _ in range(50):
                if not await EpisodeDownload.objects.filter(
                    episode=self.episode
                ).aexists():
                    break
                await asyncio.sleep(0.1)
            else:
                self.fail(
                    "EpisodeDownload row was never cleaned up after a successful download"
                )

    async def test_concurrent_requests_create_single_download_row(self):
        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        async with running_downloader():
            with patch(
                "podcasts.downloader.download_audio",
                make_mock_download(delay=0.3),
            ):
                with override_settings(MEDIA_ROOT=str(self.media_root)):
                    await asyncio.gather(
                        episode_media(req, self.episode.pk),
                        episode_media(req, self.episode.pk),
                    )

        # The unique constraint on EpisodeDownload.episode means at most one row
        # could ever have existed for this episode, regardless of the race above.
        self.assertLessEqual(
            await EpisodeDownload.objects.filter(episode=self.episode).acount(), 1
        )
