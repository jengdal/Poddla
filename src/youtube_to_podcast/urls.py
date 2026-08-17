from django.conf import settings
from django.urls import include, path

urlpatterns = [
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
