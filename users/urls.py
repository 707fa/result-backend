from django.urls import path
from .views import (
    RegisterView,
    LoginView,
    LogoutView,
    PlatformStateView,
    MeView,
    UpdateAvatarView,
    TeacherMyGroupsView,
    TeacherGroupStudentsView,
    TeacherScoreStudentView,
    TeacherScoreHistoryView,
)

urlpatterns = [
    path("auth/register", RegisterView.as_view(), name="register"),
    path("auth/login", LoginView.as_view(), name="login"),
    path("auth/logout", LogoutView.as_view(), name="logout"),
    path("platform/state", PlatformStateView.as_view(), name="platform-state"),

    path("users/me", MeView.as_view(), name="me"),
    path("users/me/avatar", UpdateAvatarView.as_view(), name="update-avatar"),
    path("teacher/history", TeacherScoreHistoryView.as_view(), name="teacher-history"),
    path("teacher/groups", TeacherMyGroupsView.as_view(), name="teacher-groups"),
    path("teacher/groups/<int:group_id>/students", TeacherGroupStudentsView.as_view(), name="teacher-group-students"),
    path(
        "teacher/groups/<int:group_id>/students/<int:student_id>/score",
        TeacherScoreStudentView.as_view(),
        name="teacher-group-student-score",
    ),
    path("teacher/score", TeacherScoreStudentView.as_view(), name="teacher-score"),
]
