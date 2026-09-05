from datetime import datetime, timezone

from django import template

register = template.Library()


@register.filter
def format_duration(seconds):
    if seconds is None:
        return ""
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@register.filter
def format_timestamp(ts):
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%-d %b %Y")


@register.filter
def strip_scheme(url):
    return url.partition("://")[2]
