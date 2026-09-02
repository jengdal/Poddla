from django.urls import path

from . import views

urlpatterns = [
    path("", views.settings, name="settings"),
    path("sse/", views.settings_sse, name="settings_sse"),
    path("feed-update-state/", views.set_feed_update_state, name="settings_set_feed_update_state"),
    path("media-files-state/", views.set_media_files_state, name="settings_set_media_files_state"),
]
