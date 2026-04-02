from django.urls import path
from .views import GlobalRatingsView, GroupRatingsView, MyRatingsView

urlpatterns = [
    path("ratings/global", GlobalRatingsView.as_view()),
    path("ratings/group/<int:group_id>", GroupRatingsView.as_view()),
    path("ratings/me", MyRatingsView.as_view()),
]