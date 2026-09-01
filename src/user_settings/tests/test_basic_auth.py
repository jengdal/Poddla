import base64

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from user_settings.basic_auth import authenticate_basic_auth, basic_auth_challenge


def _basic_auth_headers(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class AuthenticateBasicAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listener", password="not-the-basic-auth-one")
        self.user_settings = self.user.user_settings
        self.user_settings.basic_auth_password = "test-password"
        self.user_settings.save()
        self.factory = RequestFactory()

    def test_valid_credentials_return_the_user(self):
        req = self.factory.get("/", headers=_basic_auth_headers("listener", "test-password"))

        self.assertEqual(authenticate_basic_auth(req), self.user)

    def test_wrong_password_is_rejected(self):
        req = self.factory.get("/", headers=_basic_auth_headers("listener", "wrong"))

        self.assertIsNone(authenticate_basic_auth(req))

    def test_the_users_real_login_password_does_not_work(self):
        req = self.factory.get(
            "/", headers=_basic_auth_headers("listener", "not-the-basic-auth-one")
        )

        self.assertIsNone(authenticate_basic_auth(req))

    def test_unknown_username_is_rejected(self):
        req = self.factory.get("/", headers=_basic_auth_headers("nobody", "test-password"))

        self.assertIsNone(authenticate_basic_auth(req))

    def test_inactive_user_is_rejected(self):
        self.user.is_active = False
        self.user.save()
        req = self.factory.get("/", headers=_basic_auth_headers("listener", "test-password"))

        self.assertIsNone(authenticate_basic_auth(req))

    def test_missing_header_is_rejected(self):
        req = self.factory.get("/")

        self.assertIsNone(authenticate_basic_auth(req))

    def test_non_basic_scheme_is_rejected(self):
        req = self.factory.get("/", headers={"Authorization": "Bearer sometoken"})

        self.assertIsNone(authenticate_basic_auth(req))

    def test_malformed_base64_is_rejected(self):
        req = self.factory.get("/", headers={"Authorization": "Basic not-valid-base64!!"})

        self.assertIsNone(authenticate_basic_auth(req))

    def test_user_without_settings_is_rejected(self):
        user = User.objects.create_user(username="no-settings", password="whatever")
        user.user_settings.delete()
        req = self.factory.get("/", headers=_basic_auth_headers("no-settings", "whatever"))

        self.assertIsNone(authenticate_basic_auth(req))


class BasicAuthChallengeTests(TestCase):
    def test_challenge_response(self):
        response = basic_auth_challenge()

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["WWW-Authenticate"].startswith("Basic"))
