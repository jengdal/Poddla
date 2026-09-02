from typing import Any, Literal

import msgspec
from django import forms

from poddla_settings.forms import FeedUpdateForm, MediaFilesExpiryForm
from poddla_settings.models import PoddlaSettings


class SectionFormState(msgspec.Struct):
    tab_id: str
    data: dict[str, str | int] | None = None
    show_form: bool = False
    loading: bool = False

    # def sync_data(
    #     self,
    #     signals: dict[str, Any],
    #     current_settings: PoddlaSettings,
    # ) -> None:
    #     """When the form is shown, init its values, otherwise update from signals.
    #
    #     When the form is closed, we clear any data so that we can sync in actual data if it is re-opened.
    #     """
    #     form_cls = self._form_cls()
    #     self.show_form = bool(signals.get("show_form"))
    #     if not self.show_form:
    #         self.data = None
    #     elif self.data is None:
    #         self.data = form_cls.initial_from_settings(current_settings)
    #     else:
    #         self.data = signals.get("data", {})

    def form_data_signals(self) -> dict:
        """Get the values the form class expects out of the signal data"""
        data = self.data or {}
        field_names = self._form_cls().base_fields.keys()  # pyright: ignore[reportAttributeAccessIssue]

        return {name: data.get(name) for name in field_names}

    def _form_cls(self) -> type[forms.Form]:
        """Should return a django form class.

        This is a method instead of an annotated field because we don't want
        msgspec to attempt to serialize it.
        """
        raise NotImplementedError()


class FeedUpdateState(SectionFormState):
    data: dict[str, str | int] | None = None
    can_save: bool = False
    save: bool = False

    def _form_cls(self):
        return FeedUpdateForm

    def to_signals(self):
        return {
            "tab_id": self.tab_id,
            "loading": self.loading,
            "show_form": self.show_form,
            "can_save": self.can_save,
            "data": self.form_data_signals(),
            "save": self.save,
        }


class MediaFilesState(SectionFormState):
    data: dict[str, str | int] | None = None
    enabled: bool = True
    can_save: bool = False
    save: bool = False

    def _form_cls(self):
        return MediaFilesExpiryForm

    def to_signals(self):
        return {
            "tab_id": self.tab_id,
            "enabled": self.enabled,
            "loading": self.loading,
            "can_save": self.can_save,
            "show_form": self.show_form,
            "data": self.form_data_signals(),
            "save": self.save,
        }


#
# class SettingsState(msgspec.Struct):
#     """This holds the state for a browser tab on the poddla settings page."""
#
#     tab_id: str
#     loading: bool = False
#     save: Literal["feed_update", "media_files", ""] = ""
#     feed_update: FeedUpdateState = msgspec.field(default_factory=FeedUpdateState)
#     media_files: MediaFilesState = msgspec.field(default_factory=MediaFilesState)
#
#     def to_signals(self):
#         return {
#             "tab_id": self.tab_id,
#             "loading": self.loading,
#             "save": self.save,
#             "feed_update": self.feed_update.to_signals(),
#             "media_files": self.media_files.to_signals(),
#         }
