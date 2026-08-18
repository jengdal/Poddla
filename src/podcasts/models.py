from django.db import models

from youtube_to_podcast.model_publisher import ModelPublisher

channel_publisher = ModelPublisher("podcasts:channel:updates")


class Channel(models.Model):
    url = models.URLField()
    name = models.TextField()


channel_publisher.register(Channel)
