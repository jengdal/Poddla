from typing import Any

from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def authed_feed_url(context, view_name, *args: Any):
    request = context["request"]
    feed_token = request.user.user_settings.feed_token
    path = reverse(view_name, args=[feed_token, *args])
    return request.build_absolute_uri(path)
