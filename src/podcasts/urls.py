from django.urls import path

from . import views

urlpatterns = [
    path("", views.podcasts, name="podcasts"),
    path("add-channel/", views.add_channel, name="add_channel"),
    path("sse/", views.podcasts_sse, name="podcasts_sse"),
]
