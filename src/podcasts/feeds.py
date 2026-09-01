import logging

from asgiref.sync import async_to_sync
from django.contrib.auth.decorators import login_not_required
from django.contrib.syndication.views import Feed
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.feedgenerator import Rss201rev2Feed

from podcasts.downloader import refresh_podcast_feed
from user_settings.authenticated_urls import build_authenticated_url
from user_settings.basic_auth import authenticate_basic_auth, basic_auth_challenge

from .models import PodcastFeed

logger = logging.getLogger(__name__)


def _format_duration(seconds):
    if seconds is None:
        return None
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


DEFAULT_ITUNES_CATEGORY = "Society & Culture"


class PodcastRssFeed(Rss201rev2Feed):
    def rss_attributes(self):
        attrs = super().rss_attributes()
        attrs.update(
            {
                "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
                "xmlns:dc": "http://purl.org/dc/elements/1.1/",
                "xmlns:content": "http://purl.org/rss/1.0/modules/content/",
            }
        )
        return attrs

    def add_root_elements(self, handler):
        # Override the default `atom:link`.
        feed_url = self.feed.get("feed_url")
        self.feed["feed_url"] = None
        super().add_root_elements(handler)
        self.feed["feed_url"] = feed_url

        if feed_url:
            handler.addQuickElement(
                "atom:link",
                None,
                {"rel": "self", "href": feed_url, "type": "application/rss+xml"},
            )
        if image := self.feed.get("itunes_image"):
            handler.startElement("itunes:image", {"href": image})
            handler.endElement("itunes:image")
        if author := self.feed.get("itunes_author"):
            handler.addQuickElement("itunes:author", author)
        handler.addQuickElement("itunes:explicit", "false")
        handler.addQuickElement("itunes:category", None, {"text": DEFAULT_ITUNES_CATEGORY})

    def add_item_elements(self, handler, item):
        super().add_item_elements(handler, item)
        if duration := item.get("itunes_duration"):
            handler.addQuickElement("itunes:duration", duration)
        if summary := item.get("itunes_summary"):
            handler.addQuickElement("itunes:summary", summary)
            # Escape
            safe_summary = summary.replace("]]>", "]]]]><![CDATA[>")
            handler._write(f"<content:encoded><![CDATA[{safe_summary}]]></content:encoded>")
        if image := item.get("itunes_image"):
            handler.startElement("itunes:image", {"href": image})
            handler.endElement("itunes:image")


@login_not_required
class PodcastFeedRss(Feed):
    """Serves the RSS feed.

    HTTP Basic auth so that podcast apps can auth using the users basic auth
    password (UserSettings), different than the normal auth password. We don't
    want to serve this freely on the internet and doubt any podcast apps can do
    a web app login flow.
    """

    feed_type = PodcastRssFeed

    def __call__(self, request, *args, **kwargs):
        user = authenticate_basic_auth(request)
        if user is None:
            return basic_auth_challenge()
        self.auth_user = user
        return super().__call__(request, *args, **kwargs)

    def get_object(self, request, podcast_id):
        self.request = request
        podcast = get_object_or_404(PodcastFeed.objects, pk=podcast_id)
        if podcast.needs_updating():
            logger.debug("Going to update the PodcastFeed (%s)", podcast.id)
            try:
                async_to_sync(refresh_podcast_feed)(podcast=podcast)
            except Exception:
                # Just log the fail and serve what we already have.
                logger.exception(
                    "Failed to update the PodcastFeed (%s) from the source. Serving what we have.",
                    podcast.id,
                )
            podcast.refresh_from_db()
        return podcast

    def title(self, obj):
        return obj.name

    def description(self, obj):
        return obj.description or ""

    def link(self, obj):
        return reverse("podcast_feed", args=[obj.pk])

    def items(self, obj):
        return obj.episodes.order_by("-published_at")

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.show_notes

    def item_pubdate(self, item):
        return item.published_at

    def item_link(self, item):
        return item.url

    def item_enclosure_url(self, item):
        return build_authenticated_url(
            request=self.request,
            username=self.auth_user.username,
            basic_auth_password=self.auth_user.user_settings.basic_auth_password,
            view_name="podcast_episode_media",
            args=(item.pk,),
        )

    def item_enclosure_length(self, item):
        if episode_file := item.file_exists():
            return episode_file.stat().st_size
        return 0

    def item_enclosure_mime_type(self, item):
        return "audio/mp4"

    def item_extra_kwargs(self, item):
        return {
            "itunes_duration": _format_duration(item.duration),
            "itunes_summary": item.show_notes,
            "itunes_image": item.thumbnail or None,
        }

    def feed_extra_kwargs(self, obj):
        return {
            "itunes_image": obj.thumbnail or None,
            "itunes_author": obj.name,
        }
