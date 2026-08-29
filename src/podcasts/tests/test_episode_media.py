import asyncio
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import AsyncRequestFactory, TransactionTestCase, override_settings

from podcasts.downloader import _audio_download_lock
from podcasts.models import Episode, PodcastFeed, episode_publisher
from podcasts.podcast_feed_views import episode_media
from podcasts.youtube.video import DownloadInfo
from valkey_changes import valkey_client
from valkey_changes.changes import changes


def make_download(
    *,
    delay: float = 0,
    call_counter: dict | None = None,
    fail_times: int = 0,
    started: threading.Event | None = None,
):
    """Build a fake `download_audio`.

    Fails the first `fail_times` calls (to exercise the failure path) then succeeds, writing a
    small fake audio file where the real one would have gone. If `started` is given, it's set
    the moment the (simulated) download begins, so a test can wait for that instead of guessing
    at a sleep long enough to let the download actually start.
    """

    calls = {"n": 0}

    def _download(url: str, base_path: Path, file_path: Path) -> DownloadInfo:
        calls["n"] += 1
        if call_counter is not None:
            call_counter["n"] += 1
        if started is not None:
            started.set()
        if delay:
            time.sleep(delay)
        if calls["n"] <= fail_times:
            raise RuntimeError("boom")
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
        self.addCleanup(self._tmp.cleanup)
        self.media_root = Path(self._tmp.name)
        self.podcast = PodcastFeed.everything.create(
            name="Test", url="https://youtube.com/@test", source_type="channel"
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            youtube_id="abc123",
            title="Test video title",
            url="https://youtube.com/watch?v=abc123",
            show_notes="",
        )
        self.factory = AsyncRequestFactory()
        async_to_sync(_flush_valkey)()

    def tearDown(self):
        async_to_sync(_flush_valkey)()
        # A test that hits the failure path we're guarding against would otherwise leave the
        # process-wide lock held for every later test in the suite:
        if _audio_download_lock.locked():
            _audio_download_lock.release()

    async def test_single_request_downloads_and_serves(self):
        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        with (
            patch("podcasts.downloader.download_audio", make_download()),
            override_settings(MEDIA_ROOT=str(self.media_root)),
        ):
            response = await episode_media(req, self.episode.pk)

        self.assertIn(response.status_code, (200, 206))
        await self.episode.arefresh_from_db()
        self.assertIsNotNone(self.episode.file_path)

    async def test_already_downloaded_skips_download(self):
        path = self.media_root / str(self.episode.podcast_id) / f"{self.episode.id}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing audio")
        self.episode.file_path = str(path.relative_to(self.media_root))
        await self.episode.asave(update_fields=["file_path"])

        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        counter: dict = {"n": 0}
        with (
            patch("podcasts.downloader.download_audio", make_download(call_counter=counter)),
            override_settings(MEDIA_ROOT=str(self.media_root)),
        ):
            response = await episode_media(req, self.episode.pk)

        self.assertEqual(counter["n"], 0)
        self.assertIn(response.status_code, (200, 206))

    async def test_concurrent_requests_download_once(self):
        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        counter: dict = {"n": 0}
        with (
            patch(
                "podcasts.downloader.download_audio",
                make_download(delay=0.3, call_counter=counter),
            ),
            override_settings(MEDIA_ROOT=str(self.media_root)),
        ):
            r1, r2, r3 = await asyncio.gather(
                episode_media(req, self.episode.pk),
                episode_media(req, self.episode.pk),
                episode_media(req, self.episode.pk),
            )

        self.assertEqual(counter["n"], 1)
        self.assertIn(r1.status_code, (200, 206))
        self.assertIn(r2.status_code, (200, 206))
        self.assertIn(r3.status_code, (200, 206))
        # Every waiter got its lock/notify race resolved cleanly:
        self.assertFalse(_audio_download_lock.locked())

    async def test_failed_download_releases_the_lock_for_the_next_request(self):
        # Regression test: a download that raises used to leak _audio_download_lock forever,
        # wedging every later request for any episode.
        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        with (
            patch(
                "podcasts.downloader.download_audio",
                make_download(fail_times=1),
            ),
            override_settings(MEDIA_ROOT=str(self.media_root)),
        ):
            with self.assertRaises(RuntimeError):
                await episode_media(req, self.episode.pk)

            self.assertFalse(_audio_download_lock.locked())

            response = await episode_media(req, self.episode.pk)

        self.assertIn(response.status_code, (200, 206))
        await self.episode.arefresh_from_db()
        self.assertIsNotNone(self.episode.file_path)

    async def test_download_survives_request_cancellation(self):
        # Regression test: Django's ASGIHandler races every request against a
        # disconnect-listener in an asyncio.TaskGroup and cancels the request's task the
        # moment the client disconnects, even mid-download. asyncio.to_thread doesn't stop
        # the underlying thread on cancellation, so without shielding, the DB update that
        # records the finished download never happens - and the *next* request starts a
        # brand new download, unaware a copy is still running unseen in the background.
        req = self.factory.get(f"/e/{self.episode.pk}/media/")
        counter: dict = {"n": 0}
        started = threading.Event()
        episode_updates = episode_publisher.subscribe(pk=self.episode.pk)
        async with changes(episode_updates) as changed:
            with (
                patch(
                    "podcasts.downloader.download_audio",
                    make_download(delay=0.3, call_counter=counter, started=started),
                ),
                override_settings(MEDIA_ROOT=str(self.media_root)),
            ):
                task = asyncio.create_task(episode_media(req, self.episode.pk))
                await asyncio.to_thread(started.wait, 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

                # The download is shielded from the request's cancellation, so it keeps
                # running (and eventually saves) after the request itself has died:
                await asyncio.wait_for(changed.wait(), timeout=5)

                # The pubsub wake-up and the shielded task's own `finally` releasing the
                # lock are two independent things racing on the event loop - the
                # notification isn't proof the lock is free yet, so poll briefly instead
                # of asserting it outright:
                for _ in range(50):
                    if not _audio_download_lock.locked():
                        break
                    await asyncio.sleep(0.02)
                else:
                    self.fail(
                        "_audio_download_lock was never released after the shielded download finished"
                    )
                await self.episode.arefresh_from_db()
                self.assertIsNotNone(self.episode.file_path)

                # A fresh request must find the file already there, not download it again:
                response = await episode_media(req, self.episode.pk)

        self.assertEqual(counter["n"], 1)
        self.assertIn(response.status_code, (200, 206))
