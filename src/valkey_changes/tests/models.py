from django.db import models

from valkey_changes.model_publisher import ModelPublisher

publisher = ModelPublisher("valkey_changes:test:models")


class Thing(models.Model):
    """A model for the ModelPublisher tests"""

    name = models.CharField(max_length=100, default="")

    class Meta:
        # This isn't automatic, because the model isn't in the app roots models.py:
        app_label = "valkey_changes"


publisher.register(Thing)
