from django.test import SimpleTestCase

from poddla_settings.forms import FeedUpdateForm, MediaFilesExpiryForm


class FeedUpdateFormTests(SimpleTestCase):
    def test_valid_at_threshold(self):
        form = FeedUpdateForm(data={"hours": 0, "minutes": 5})
        self.assertTrue(form.is_valid())

    def test_valid_above_threshold(self):
        form = FeedUpdateForm(data={"hours": 1, "minutes": 0})
        self.assertTrue(form.is_valid())

    def test_invalid_below_threshold(self):
        form = FeedUpdateForm(data={"hours": 0, "minutes": 4})
        self.assertFalse(form.is_valid())
        self.assertTrue(form.non_field_errors())

    def test_invalid_zero_duration(self):
        form = FeedUpdateForm(data={"hours": 0, "minutes": 0})
        self.assertFalse(form.is_valid())
        self.assertTrue(form.non_field_errors())

    def test_field_error_does_not_crash_clean(self):
        # hours fails its own min_value=0 validator, so it's absent from
        # cleaned_data - clean() must not try to do arithmetic with it.
        form = FeedUpdateForm(data={"hours": -1, "minutes": 5})
        self.assertFalse(form.is_valid())
        self.assertIn("hours", form.errors)
        self.assertFalse(form.non_field_errors())


class MediaFilesExpiryFormTests(SimpleTestCase):
    def test_valid_at_threshold(self):
        form = MediaFilesExpiryForm(data={"weeks": 0, "days": 1})
        self.assertTrue(form.is_valid())

    def test_valid_above_threshold(self):
        form = MediaFilesExpiryForm(data={"weeks": 1, "days": 0})
        self.assertTrue(form.is_valid())

    def test_invalid_below_threshold(self):
        form = MediaFilesExpiryForm(data={"weeks": 0, "days": 0})
        self.assertFalse(form.is_valid())
        self.assertTrue(form.non_field_errors())

    def test_field_error_does_not_crash_clean(self):
        form = MediaFilesExpiryForm(data={"weeks": -1, "days": 1})
        self.assertFalse(form.is_valid())
        self.assertIn("weeks", form.errors)
        self.assertFalse(form.non_field_errors())
