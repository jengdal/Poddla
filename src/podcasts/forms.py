from django import forms
from django.core.exceptions import ValidationError

from podcasts.models import Channel


def validate_youtube_url(value):
    if not value.startswith("https://youtube.com"):
        raise ValidationError("URL must start with https://youtube.com")


class ChannelForm(forms.ModelForm):
    url = forms.URLField(validators=[validate_youtube_url])

    class Meta:
        model = Channel
        fields = ("url",)
