import secrets

from django.conf import settings
from django.db import models

# Letters and numbers that can be confused with each other are removed:
PASSWORD_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # nosec B105
PASSWORD_LENGTH = 20
PASSWORD_GROUP_SIZE = 5


def generate_basic_auth_password() -> str:
    raw = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))
    groups = [raw[i : i + PASSWORD_GROUP_SIZE] for i in range(0, len(raw), PASSWORD_GROUP_SIZE)]
    return "-".join(groups)


class UserSettings(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_settings"
    )

    # Used for HTTP Basic Auth on the RSS feed and episode media:
    basic_auth_password = models.CharField(max_length=64, default=generate_basic_auth_password)

    updated_at = models.DateTimeField(auto_now=True)

    def regenerate_basic_auth_password(self) -> str:
        self.basic_auth_password = generate_basic_auth_password()
        self.save(update_fields=["basic_auth_password", "updated_at"])
        return self.basic_auth_password

    def __str__(self):
        return f"Settings for {self.user}"
