from typing import Any

from django import template

from ..authenticated_urls import build_authenticated_url

register = template.Library()


@register.simple_tag(takes_context=True)
def authed_feed_url(context, view_name, *args: Any):
    request = context["request"]
    return build_authenticated_url(
        request=request,
        username=request.user.username,
        basic_auth_password=request.user.user_settings.basic_auth_password,
        view_name=view_name,
        args=args,
    )
