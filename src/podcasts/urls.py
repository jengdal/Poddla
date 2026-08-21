from django.urls import path

from . import add_channel_views, podcast_feed_views, podcasts_views
from .feeds import PodcastFeedRss

urlpatterns = [
    path("", podcasts_views.podcasts, name="podcasts"),
    path("sse/", podcasts_views.podcasts_sse, name="podcasts_sse"),
    path(
        "p/<int:podcast_id>/",
        podcast_feed_views.podcast_feed,
        name="podcast_feed",
    ),
    path(
        "p/<int:podcast_id>/sse/",
        podcast_feed_views.podcast_feed_sse,
        name="podcast_feed_sse",
    ),
    path(
        "p/<int:podcast_id>/rss/",
        PodcastFeedRss(),
        name="podcast_feed_rss",
    ),
    path(
        "e/<int:episode_id>/media/",
        podcast_feed_views.episode_media,
        name="podcast_episode_media",
    ),
    path("add-channel/", add_channel_views.add_channel, name="add_channel"),
    path("add-channel/sse/", add_channel_views.add_channel_sse, name="add_channel_sse"),
    path(
        "add-channel/state/", add_channel_views.set_state, name="add_channel_set_state"
    ),
]
