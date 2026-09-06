from django.contrib.auth.base_user import AbstractBaseUser

from .models import UserSettings


async def authenticate_feed_token(feed_token: str) -> AbstractBaseUser | None:
    """Return the user whose token this is."""
    try:
        user_settings = await UserSettings.objects.select_related("user").aget(
            feed_token=feed_token, user__is_active=True
        )
    except UserSettings.DoesNotExist:
        return None
    return user_settings.user
