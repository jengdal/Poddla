from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listener", password="old-password-123")
        self.client = Client()
        self.client.force_login(self.user)

    def test_anonymous_users_are_redirected_to_login(self):
        client = Client()
        url = reverse("password_change")

        response = client.get(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)

    def test_successful_password_change_redirects_to_done_page(self):
        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "old-password-123",
                "new_password1": "a-much-better-p4ssword",
                "new_password2": "a-much-better-p4ssword",
            },
        )

        self.assertRedirects(response, reverse("password_change_done"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-much-better-p4ssword"))

    def test_wrong_old_password_shows_field_error(self):
        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "wrong-password",
                "new_password1": "a-much-better-p4ssword",
                "new_password2": "a-much-better-p4ssword",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "old_password",
            "Your old password was entered incorrectly. Please enter it again.",
        )
