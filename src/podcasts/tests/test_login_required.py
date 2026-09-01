from django.test import Client, TestCase
from django.urls import reverse


class LoginRequiredTests(TestCase):
    def test_anonymous_users_are_redirected_to_login(self):
        client = Client()
        url = reverse("podcasts")

        response = client.get(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)
