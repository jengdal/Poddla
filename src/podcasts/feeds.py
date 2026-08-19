from django.contrib.syndication.views import Feed
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.feedgenerator import Rss201rev2Feed

from .models import PodcastFeed


def _format_duration(seconds):
    if seconds is None:
        return None
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class PodcastRssFeed(Rss201rev2Feed):
    def rss_attributes(self):
        attrs = super().rss_attributes()
        attrs.update(
            {
                "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
                "xmlns:dc": "http://purl.org/dc/elements/1.1/",
                "xmlns:content": "http://purl.org/rss/1.0/modules/content/",
                "xmlns:atom": "http://www.w3.org/2005/Atom/",
            }
        )
        return attrs

    def add_root_elements(self, handler):
        super().add_root_elements(handler)
        if image := self.feed.get("itunes_image"):
            handler.startElement("itunes:image", {"href": image})
            handler.endElement("itunes:image")
        if author := self.feed.get("itunes_author"):
            handler.addQuickElement("itunes:author", author)
        handler.addQuickElement("itunes:explicit", "no")

    def add_item_elements(self, handler, item):
        super().add_item_elements(handler, item)
        if duration := item.get("itunes_duration"):
            handler.addQuickElement("itunes:duration", duration)
        if summary := item.get("itunes_summary"):
            handler.addQuickElement("itunes:summary", summary)
            handler.addQuickElement("content:encoded", summary)
        if image := item.get("itunes_image"):
            handler.startElement("itunes:image", {"href": image})
            handler.endElement("itunes:image")


class PodcastFeedRss(Feed):
    feed_type = PodcastRssFeed

    def get_object(self, request, podcast_id):
        return get_object_or_404(PodcastFeed.objects, pk=podcast_id)

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
