from django import forms
from django.core.exceptions import ValidationError

from podcasts.models import PodcastFeed


def validate_youtube_url(value):
    if not (value.startswith("https://youtube.com") or value.startswith("https://www.youtube.com")):
        raise ValidationError("URL must start with https://www.youtube.com")


def validate_unique(value):
    if PodcastFeed.objects.filter(url=value).exists():
        raise ValidationError("We already track that URL.")


class FeedForm(forms.Form):
    url = forms.URLField(
        # The client side validation of this prevents submission, we want all validation on the
        # server:
        required=False,
        validators=[validate_youtube_url, validate_unique],
        widget=forms.URLInput(
            attrs={
                # type=text gets rid of the client side validation. An invalid form won't get @post'ed.
                "type": "text",
                "class": "ui-text-input",
                "placeholder": "https://www.youtube.com/channel/...",
            }
        ),
    )
