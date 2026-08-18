from django import forms
from django.core.exceptions import ValidationError

from podcasts.models import Channel


def validate_youtube_url(value):
    if not value.startswith("https://youtube.com"):
        raise ValidationError("URL must start with https://youtube.com")


class ChannelForm(forms.ModelForm):
    url = forms.URLField(
        validators=[validate_youtube_url],
        widget=forms.URLInput(
            attrs={
                # This gets rid of the client side validation, and it also makes the keyup post work:
                "type": "text"
            }
        ),
    )

    class Meta:
        model = Channel
        fields = ("url",)
