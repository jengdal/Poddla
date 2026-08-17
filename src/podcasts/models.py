from django.db import models


class Channel(models.Model):
    url = models.URLField()
    name = models.TextField()
