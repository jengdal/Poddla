import asyncio
import logging

from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import aget_object_or_404
from django.urls import reverse
from django.utils.feedgenerator import Enclosure, Rss201rev2Feed
from django.utils.http import http_date

from podcasts.downloader import refresh_podcast_feed_task
from user_settings.feed_auth import authenticate_feed_token

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
async def podcast_feed_rss(request: HttpRequest, feed_token: str, podcast_id: int):
    """Serves the RSS feed."""
    user = await authenticate_feed_token(feed_token)
    if user is None:
        raise Http404

    podcast = await aget_object_or_404(PodcastFeed.objects, pk=podcast_id)
    if await podcast.aneeds_updating():
        logger.debug("Going to update the PodcastFeed (%s)", podcast.id)
        update_task = refresh_podcast_feed_task(podcast=podcast)

        # Wait for the task. If the request gets cancelled `shield` will
        # protect it from also being cancelled, we want it to complete so that
        # it's fresh when the client retries.
        await asyncio.shield(update_task)
        await podcast.arefresh_from_db()

    feed = PodcastRssFeed(
        title=podcast.name,
        link=request.build_absolute_uri(reverse("podcast_feed", args=[podcast.pk])),
        description=podcast.description or "",
        feed_url=request.build_absolute_uri(request.path),
        itunes_image=podcast.thumbnail or None,
        itunes_author=podcast.name,
    )

    async for episode in podcast.episodes.order_by("-published_at"):
        episode_file = await episode.file_exists()
        if episode_file:
            enclosure_length = episode_file.stat().st_size
        else:
            enclosure_length = 0
        enclosure_url = request.build_absolute_uri(
            reverse(
                "podcast_episode_media", kwargs={"feed_token": feed_token, "episode_id": episode.pk}
            )
        )
        feed.add_item(
            title=episode.title,
            link=episode.url,
            description=episode.show_notes,
            pubdate=episode.published_at,
            unique_id=episode.url,
            enclosures=[
                Enclosure(url=enclosure_url, length=str(enclosure_length), mime_type="audio/mp4")
            ],
            itunes_duration=_format_duration(episode.duration),
            itunes_summary=episode.show_notes,
            itunes_image=episode.thumbnail or None,
        )

    response = HttpResponse(content_type=feed.content_type)
    response.headers["Last-Modified"] = http_date(feed.latest_post_date().timestamp())
    feed.write(response, "utf-8")
    return response
