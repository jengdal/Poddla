import secrets

from django.conf import settings
from django.db import models

# Letters and numbers that can be confused with each other are removed:
FEED_TOKEN_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # nosec B105
FEED_TOKEN_LENGTH = 20
FEED_TOKEN_GROUP_SIZE = 5


def generate_feed_token() -> str:
    raw = "".join(secrets.choice(FEED_TOKEN_ALPHABET) for _ in range(FEED_TOKEN_LENGTH))
    groups = [raw[i : i + FEED_TOKEN_GROUP_SIZE] for i in range(0, len(raw), FEED_TOKEN_GROUP_SIZE)]
    return "-".join(groups)


class UserSettings(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_settings"
    )

    # Used to authenticate the private RSS feed and episode media URLs:
    feed_token = models.CharField(max_length=64, default=generate_feed_token, unique=True)

    updated_at = models.DateTimeField(auto_now=True)

    def regenerate_feed_token(self) -> str:
        self.feed_token = generate_feed_token()
        self.save(update_fields=["feed_token", "updated_at"])
        return self.feed_token

    def __str__(self):
        return f"Settings for {self.user}"
