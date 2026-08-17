from django import forms

from podcasts.models import Channel


class ChannelForm(forms.ModelForm):
    class Meta:
        model = Channel
        fields = ("url",)
