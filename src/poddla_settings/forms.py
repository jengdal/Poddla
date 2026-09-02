import datetime

from django import forms

from poddla_settings.models import PoddlaSettings


class FeedUpdateForm(forms.Form):
    hours = forms.IntegerField(
        min_value=0,
    )
    minutes = forms.IntegerField(
        min_value=0,
    )

    def to_model_kwargs(self) -> dict:
        """Convert validated form data into PoddlaSettings values."""
        data = self.cleaned_data

        return {
            "min_feed_update_freq": datetime.timedelta(
                hours=data["hours"],
                minutes=data["minutes"],
            ),
        }

    @classmethod
    def initial_from_settings(cls, settings: PoddlaSettings) -> dict:
        """PoddlaSetting instance to form field values."""
        hours, freq_remainder = divmod(int(settings.min_feed_update_freq.total_seconds()), 3600)
        minutes = freq_remainder // 60
        return {
            "hours": hours,
            "minutes": minutes,
        }


class MediaFilesExpiryForm(forms.Form):
    weeks = forms.IntegerField(
        min_value=0,
    )
    days = forms.IntegerField(
        min_value=0,
    )

    def to_model_kwargs(self) -> dict:
        """Convert validated form data into PoddlaSettings values."""
        data = self.cleaned_data

        media_files_expire = datetime.timedelta(
            weeks=data["weeks"],
            days=data["days"],
        )

        return {
            "media_files_expiry_enabled": True,
            "media_files_expire": media_files_expire,
        }

    @classmethod
    def initial_from_settings(cls, settings: PoddlaSettings) -> dict:
        """PoddlaSetting instance to form field values."""
        expire = settings.media_files_expire
        if expire:
            expire_weeks, expire_days = divmod(expire.days, 7)
        else:
            expire_weeks = expire_days = 0

        return {
            "weeks": expire_weeks,
            "days": expire_days,
        }
