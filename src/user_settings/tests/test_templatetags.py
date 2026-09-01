from django.contrib.auth.models import User
from django.template import RequestContext, Template
from django.test import RequestFactory, TestCase


def render_tag(request, *args):
    args_source = " ".join(repr(arg) if isinstance(arg, str) else str(arg) for arg in args)
    template = Template("{% load user_settings_tags %}{% authed_feed_url " + args_source + " %}")
    return template.render(RequestContext(request))


class AuthenticatedFeedUrlTagTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listener", password="a-real-password")
        self.factory = RequestFactory()

    def test_embeds_the_username_and_password_in_the_url(self):
        request = self.factory.get("/")
        request.user = self.user

        rendered = render_tag(request, "podcast_feed_rss", 1)

        self.assertEqual(
            rendered,
            "http://testserver/p/1/rss/".replace(
                "://", f"://listener:{self.user.user_settings.basic_auth_password}@"
            ),
        )
