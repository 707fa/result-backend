from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenRefreshView
from users.views import HealthView
import os


admin.site.site_header = "Iman Teacher Panel"
admin.site.site_title = "Iman Admin"
admin.site.index_title = "Teacher workspace"
admin.site.enable_nav_sidebar = False


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_refresh"


ENABLE_LEGACY_ROOT_API = getattr(settings, "DEBUG", False) or (
    str(os.environ.get("ENABLE_LEGACY_ROOT_API", "") or "").strip().lower() in {"1", "true", "yes", "on"}
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("users.urls")),
    path("api/", include("ratings.urls")),
    path("api/token/refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh"),
    path("health", HealthView.as_view(), name="health_root"),
]

if ENABLE_LEGACY_ROOT_API:
    urlpatterns += [
        path("", include("users.urls")),
        path("", include("ratings.urls")),
        path("token/refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh_root"),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
