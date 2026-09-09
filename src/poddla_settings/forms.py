import datetime

from django import forms

from poddla_settings.models import PoddlaSettings

MIN_FEED_UPDATE_FREQ_MINUTES = 5
MIN_MEDIA_FILES_EXPIRE_DAYS = 1


class FeedUpdateForm(forms.Form):
    hours = forms.IntegerField(
        min_value=0,
    )
    minutes = forms.IntegerField(
        min_value=0,
    )

    def clean(self):
        cleaned_data = super().clean()
        hours = cleaned_data.get("hours", 0)
        minutes = cleaned_data.get("minutes", 0)
        if hours is not None and minutes is not None:
            duration = datetime.timedelta(hours=hours, minutes=minutes)
            if duration < datetime.timedelta(minutes=MIN_FEED_UPDATE_FREQ_MINUTES):
                raise forms.ValidationError(
                    f"Feeds can be refreshed at most every {MIN_FEED_UPDATE_FREQ_MINUTES} minutes."
                )
        return cleaned_data

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

    def clean(self):
        cleaned_data = super().clean()
        weeks = cleaned_data.get("weeks")
        days = cleaned_data.get("days")
        if weeks is not None and days is not None:
            duration = datetime.timedelta(weeks=weeks, days=days)
            if duration < datetime.timedelta(days=MIN_MEDIA_FILES_EXPIRE_DAYS):
                raise forms.ValidationError(
                    f"Media files must be kept for at least {MIN_MEDIA_FILES_EXPIRE_DAYS} day(s)."
                )
        return cleaned_data

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
