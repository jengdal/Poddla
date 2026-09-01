from django.test import RequestFactory, TestCase

from user_settings.authenticated_urls import build_authenticated_url


class BuildAuthenticatedUrlTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_embeds_the_username_and_password_in_the_url(self):
        request = self.factory.get("/")

        url = build_authenticated_url(
            request=request,
            username="listener",
            basic_auth_password="s3cret",
            view_name="podcast_feed_rss",
            args=(1,),
        )

        self.assertEqual(url, "http://listener:s3cret@testserver/p/1/rss/")

    def test_url_encodes_special_characters_in_the_credentials(self):
        request = self.factory.get("/")

        url = build_authenticated_url(
            request=request,
            username="a b",
            basic_auth_password="p@ss/word",
            view_name="podcast_feed_rss",
            args=(1,),
        )

        self.assertEqual(url, "http://a%20b:p%40ss%2Fword@testserver/p/1/rss/")
