from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings


@override_settings(INITIAL_ADMIN_PASSWORD="a-generated-password")
class CreateInitialAdminTests(TestCase):
    def test_creates_the_admin_user(self):
        call_command("create_initial_admin")

        admin = User.objects.get(username="admin")
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.check_password("a-generated-password"))

    @override_settings(INITIAL_ADMIN_PASSWORD="")
    def test_does_nothing_without_a_password(self):
        call_command("create_initial_admin")

        self.assertFalse(User.objects.filter(username="admin").exists())

    def test_an_existing_admin_users_password_is_never_touched(self):
        admin = User.objects.create_superuser(
            username="admin", email="", password="the-original-password"
        )

        with override_settings(INITIAL_ADMIN_PASSWORD="a-different-password"):
            call_command("create_initial_admin")

        admin.refresh_from_db()
        self.assertTrue(admin.check_password("the-original-password"))
        self.assertFalse(admin.check_password("a-different-password"))

    def test_running_it_twice_only_creates_one_user(self):
        call_command("create_initial_admin")
        call_command("create_initial_admin")

        self.assertEqual(User.objects.filter(username="admin").count(), 1)
