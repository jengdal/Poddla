import msgspec


class SectionFormState(msgspec.Struct):
    tab_id: str
    data: dict[str, str | int] | None = None
    show_form: bool = False
    loading: bool = False


class FeedUpdateState(SectionFormState):
    data: dict[str, str | int] | None = None
    can_save: bool = False
    save: bool = False

    def to_signals(self):
        data = self.data or {}
        return {
            "tab_id": self.tab_id,
            "loading": self.loading,
            "show_form": self.show_form,
            "can_save": self.can_save,
            "data": {"hours": data.get("hours"), "minutes": data.get("minutes")},
            "save": self.save,
        }


class MediaFilesState(SectionFormState):
    data: dict[str, str | int] | None = None
    enabled: bool = True
    can_save: bool = False
    save: bool = False

    def to_signals(self):
        data = self.data or {}
        return {
            "tab_id": self.tab_id,
            "enabled": self.enabled,
            "loading": self.loading,
            "can_save": self.can_save,
            "show_form": self.show_form,
            "data": {"weeks": data.get("weeks"), "days": data.get("days")},
            "save": self.save,
        }
