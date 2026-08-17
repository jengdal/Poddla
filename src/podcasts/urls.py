from django.urls import path

from . import views

urlpatterns = [
    path("", views.podcasts, name="podcasts"),
]
