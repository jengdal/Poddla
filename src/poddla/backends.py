from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UserSettingsModelBackend(ModelBackend):
    """Automatically includes `user_settings` on User objects when authenticating."""

    def get_user(self, user_id):
        User = get_user_model()
        try:
            user = User._default_manager.select_related("user_settings").get(pk=user_id)
        except User.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None
