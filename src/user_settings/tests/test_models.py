from django.contrib.auth.models import User
from django.test import TestCase

from user_settings.models import FEED_TOKEN_ALPHABET, generate_feed_token


class GenerateFeedTokenTests(TestCase):
    def test_token_shape(self):
        token = generate_feed_token()

        groups = token.split("-")
        self.assertEqual(len(groups), 4)
        for group in groups:
            self.assertEqual(len(group), 5)
            for char in group:
                self.assertIn(char, FEED_TOKEN_ALPHABET)

    def test_tokens_are_not_predictable(self):
        tokens = {generate_feed_token() for _ in range(20)}

        self.assertEqual(len(tokens), 20)


class UserSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listener", password="whatever")
        self.user_settings = self.user.user_settings

    def test_a_token_is_generated_by_default(self):
        self.assertTrue(self.user_settings.feed_token)

    def test_regenerate_replaces_the_token_and_persists_it(self):
        old_token = self.user_settings.feed_token

        new_token = self.user_settings.regenerate_feed_token()

        self.assertNotEqual(new_token, old_token)
        self.user_settings.refresh_from_db()
        self.assertEqual(self.user_settings.feed_token, new_token)
