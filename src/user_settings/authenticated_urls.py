from typing import Any
from urllib.parse import quote

from django.urls import reverse


def build_authenticated_url(
    request, username: str, basic_auth_password: str, view_name: str, args: tuple[Any]
):
    """Return the full URL to `view_name` with the users basic auth creds included.

    Example: https://username:basic_auth_password@poddla.example.com/p/1/rss/
    """

    path = reverse(view_name, args=args)
    absolute_url = request.build_absolute_uri(path)
    username = quote(username, safe="")
    password = quote(basic_auth_password, safe="")
    scheme, sep, rest = absolute_url.partition("://")
    return f"{scheme}{sep}{username}:{password}@{rest}"
