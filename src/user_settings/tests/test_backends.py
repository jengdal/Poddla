from django.contrib.auth.models import User
from django.test import TestCase

from poddla.backends import UserSettingsModelBackend


class UserSettingsModelBackendTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listener", password="a-real-password")
        self.backend = UserSettingsModelBackend()

    def test_get_user_select_relates_user_settings_in_one_query(self):
        with self.assertNumQueries(1):
            user = self.backend.get_user(self.user.pk)
            _ = user.user_settings.basic_auth_password

        self.assertEqual(
            user.user_settings.basic_auth_password, self.user.user_settings.basic_auth_password
        )

    def test_unknown_user_id_returns_none(self):
        self.assertIsNone(self.backend.get_user(self.user.pk + 1000))

    def test_inactive_user_returns_none(self):
        self.user.is_active = False
        self.user.save()

        self.assertIsNone(self.backend.get_user(self.user.pk))
