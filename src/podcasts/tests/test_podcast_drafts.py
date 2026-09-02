from django.test import TestCase

from podcasts.models import Episode, PodcastFeed
from podcasts.youtube.feed import FeedSource, VideoInfo


def make_feed(videos: list[VideoInfo]) -> FeedSource:
    return FeedSource(
        url="https://youtube.com/@test",
        title="A test channel",
        source_type="channel",
        videos=videos,
        description="A description",
        thumbnail="https://example.com/thumb.jpg",
    )


class CreateDraftTests(TestCase):
    def test_creates_a_draft_podcast_with_mapped_fields(self):
        feed = make_feed([])

        podcast = PodcastFeed.drafts.create_draft(feed)

        self.assertEqual(podcast.status, PodcastFeed.STATUS_DRAFT)
        self.assertEqual(podcast.source_type, "channel")
        self.assertEqual(podcast.url, "https://youtube.com/@test")
        self.assertEqual(podcast.name, "A test channel")
        self.assertEqual(podcast.description, "A description")
        self.assertEqual(podcast.thumbnail, "https://example.com/thumb.jpg")
        self.assertTrue(PodcastFeed.drafts.filter(pk=podcast.pk).exists())

    def test_falls_back_to_empty_strings_for_missing_description_and_thumbnail(self):
        feed = make_feed([])
        feed.description = None
        feed.thumbnail = None

        podcast = PodcastFeed.drafts.create_draft(feed)

        self.assertEqual(podcast.description, "")
        self.assertEqual(podcast.thumbnail, "")

    def test_bulk_creates_an_episode_per_video(self):
        feed = make_feed(
            [
                VideoInfo(
                    id="v1",
                    title="Video one",
                    url="https://youtube.com/watch?v=v1",
                    duration=100,
                    thumbnail="https://example.com/v1.jpg",
                    timestamp=1_700_000_000,
                ),
                VideoInfo(
                    id="v2",
                    title="Video two",
                    url="https://youtube.com/watch?v=v2",
                    duration=None,
                    thumbnail=None,
                    timestamp=None,
                ),
            ]
        )

        podcast = PodcastFeed.drafts.create_draft(feed)

        episodes = Episode.objects.filter(podcast=podcast).order_by("youtube_id")
        self.assertEqual(episodes.count(), 2)

        v1 = episodes.get(youtube_id="v1")
        self.assertEqual(v1.title, "Video one")
        self.assertEqual(v1.url, "https://youtube.com/watch?v=v1")
        self.assertEqual(v1.duration, 100)
        self.assertEqual(v1.thumbnail, "https://example.com/v1.jpg")
        self.assertIsNotNone(v1.published_at)

        v2 = episodes.get(youtube_id="v2")
        self.assertEqual(v2.title, "Video two")
        self.assertEqual(v2.duration, None)
        self.assertEqual(v2.thumbnail, "")
        self.assertIsNone(v2.published_at)


class PublishTests(TestCase):
    def test_publishes_an_existing_draft(self):
        draft = PodcastFeed.drafts.create_draft(make_feed([]))

        published = PodcastFeed.drafts.publish(draft.pk)

        self.assertEqual(published.pk, draft.pk)
        self.assertEqual(published.status, PodcastFeed.STATUS_PUBLIC)
        self.assertTrue(PodcastFeed.objects.filter(pk=draft.pk).exists())
        self.assertFalse(PodcastFeed.drafts.filter(pk=draft.pk).exists())

    def test_raises_for_a_missing_pk(self):
        with self.assertRaises(PodcastFeed.DoesNotExist):
            PodcastFeed.drafts.publish(999999)

    def test_raises_for_a_pk_that_is_already_public(self):
        draft = PodcastFeed.drafts.create_draft(make_feed([]))
        PodcastFeed.drafts.publish(draft.pk)

        with self.assertRaises(PodcastFeed.DoesNotExist):
            PodcastFeed.drafts.publish(draft.pk)
