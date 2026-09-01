import base64
import binascii
import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest, HttpResponse

from .models import UserSettings

REALM = "Poddla"


def authenticate_basic_auth(request: HttpRequest) -> AbstractBaseUser | None:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "basic" or not credentials:
        return None

    try:
        decoded = base64.b64decode(credentials).decode("utf-8")
    except binascii.Error, UnicodeDecodeError:
        return None
    username, _, password = decoded.partition(":")
    if not username or not password:
        return None

    User = get_user_model()
    try:
        user = User.objects.select_related("user_settings").get(
            **{User.USERNAME_FIELD: username}, is_active=True
        )
        user_settings = user.user_settings
    except User.DoesNotExist, UserSettings.DoesNotExist:
        return None

    if secrets.compare_digest(user_settings.basic_auth_password, password):
        return user
    return None


def basic_auth_challenge() -> HttpResponse:
    response = HttpResponse("Authentication required.", status=401)
    response["WWW-Authenticate"] = f'Basic realm="{REALM}"'
    return response
