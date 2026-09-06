from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.test import TestCase

from user_settings.feed_auth import authenticate_feed_token
from user_settings.models import UserSettings


class AuthenticateFeedTokenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listener", password="a-real-password")
        self.user_settings = self.user.user_settings
        self.user_settings.feed_token = "test-token"
        self.user_settings.save()

    async def test_valid_token_returns_the_user(self):
        self.assertEqual(await authenticate_feed_token("test-token"), self.user)

    async def test_wrong_token_is_rejected(self):
        self.assertIsNone(await authenticate_feed_token("wrong"))

    async def test_empty_token_is_rejected(self):
        self.assertIsNone(await authenticate_feed_token(""))

    async def test_inactive_user_is_rejected(self):
        self.user.is_active = False
        await self.user.asave()

        self.assertIsNone(await authenticate_feed_token("test-token"))

    async def test_user_without_settings_is_rejected(self):
        user = await sync_to_async(User.objects.create_user)(
            username="no-settings", password="whatever"
        )
        user_settings = await UserSettings.objects.aget(user=user)
        token = user_settings.feed_token
        await user_settings.adelete()

        self.assertIsNone(await authenticate_feed_token(token))
