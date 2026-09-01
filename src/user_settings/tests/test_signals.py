from django.contrib.auth.models import User
from django.test import TestCase

from user_settings.models import UserSettings


class CreateUserSettingsSignalTests(TestCase):
    def test_creating_a_user_creates_their_settings(self):
        user = User.objects.create_user(username="listener", password="a-real-password")

        self.assertTrue(UserSettings.objects.filter(user=user).exists())

    def test_saving_an_existing_user_again_does_not_duplicate_settings(self):
        user = User.objects.create_user(username="listener", password="a-real-password")

        user.first_name = "Changed"
        user.save()

        self.assertEqual(UserSettings.objects.filter(user=user).count(), 1)
