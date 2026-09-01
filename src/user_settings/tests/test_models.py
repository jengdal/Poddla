from django.contrib.auth.models import User
from django.test import TestCase

from user_settings.models import PASSWORD_ALPHABET, generate_basic_auth_password


class GenerateBasicAuthPasswordTests(TestCase):
    def test_password_shape(self):
        password = generate_basic_auth_password()

        groups = password.split("-")
        self.assertEqual(len(groups), 4)
        for group in groups:
            self.assertEqual(len(group), 5)
            for char in group:
                self.assertIn(char, PASSWORD_ALPHABET)

    def test_passwords_are_not_predictable(self):
        passwords = {generate_basic_auth_password() for _ in range(20)}

        self.assertEqual(len(passwords), 20)


class UserSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listener", password="whatever")
        self.user_settings = self.user.user_settings

    def test_a_password_is_generated_by_default(self):
        self.assertTrue(self.user_settings.basic_auth_password)

    def test_regenerate_replaces_the_password_and_persists_it(self):
        old_password = self.user_settings.basic_auth_password

        new_password = self.user_settings.regenerate_basic_auth_password()

        self.assertNotEqual(new_password, old_password)
        self.user_settings.refresh_from_db()
        self.assertEqual(self.user_settings.basic_auth_password, new_password)
