from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "accounts/password_change/",
        auth_views.PasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "accounts/password_change/done/",
        auth_views.PasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    path("settings/", include("poddla_settings.urls")),
    path("", include("podcasts.urls")),
]

if settings.DEBUG:
    from debug_toolbar.toolbar import debug_toolbar_urls
    from django.conf.urls.static import static
    from django.contrib.auth.decorators import login_not_required
    from django.contrib.staticfiles.views import serve as serve_static

    urlpatterns += static(settings.STATIC_URL, view=login_not_required(serve_static))

    urlpatterns += debug_toolbar_urls()
    urlpatterns += [
        path("__reload__/", include("django_browser_reload.urls")),
    ]
