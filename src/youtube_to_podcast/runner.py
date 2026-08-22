from pathlib import Path

from django.test.runner import DiscoverRunner

SRC_DIR = str(Path(__file__).resolve().parent.parent)


class Runner(DiscoverRunner):
    def build_suite(self, test_labels=None, **kwargs):
        # Find the django apps and their tests within the src directory. The default runner doesn't do that.
        return super().build_suite(test_labels or [SRC_DIR], **kwargs)
