from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Create the initial 'admin' superuser from INITIAL_ADMIN_PASSWORD, if it doesn't exist "
        "yet. Safe to run on every startup: does nothing once the user exists, even if "
        "INITIAL_ADMIN_PASSWORD later changes."
    )

    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.filter(username="admin").exists():
            self.stdout.write("The 'admin' user already exists, skipping.")
            return

        if not settings.INITIAL_ADMIN_PASSWORD:
            self.stdout.write(
                self.style.WARNING(
                    "The 'admin' user does not exist yet. Please set INITIAL_ADMIN_PASSWORD, restart, and it will get created."
                )
            )
            return

        User.objects.create_superuser(
            username="admin", email="", password=settings.INITIAL_ADMIN_PASSWORD
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Created the initial 'admin' superuser. Now please change the password in the UI."
            )
        )
