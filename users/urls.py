from django.urls import path
from .views import (
    RegisterView,
    LoginView,
    LogoutView,
    PlatformStateView,
    MeView,
    UserProfileDetailView,
    ProgressMeView,
    TeacherStudentProgressView,
    UpdateAvatarView,
    TeacherMyGroupsView,
    TeacherGroupStudentsView,
    TeacherDeactivateStudentView,
    TeacherScoreStudentView,
    TeacherScoreHistoryView,
    AiChatMessagesView,
    FriendlyConversationsView,
    FriendlyConversationMessagesView,
    GrammarTopicsView,
    SupportTicketListCreateView,
    SupportTicketUpdateView,
)

urlpatterns = [
    path("auth/register", RegisterView.as_view(), name="register"),
    path("auth/login", LoginView.as_view(), name="login"),
    path("auth/logout", LogoutView.as_view(), name="logout"),
    path("platform/state", PlatformStateView.as_view(), name="platform-state"),

    path("users/me", MeView.as_view(), name="me"),
    path("users/me/avatar", UpdateAvatarView.as_view(), name="update-avatar"),
    path("users/profile/<int:user_id>", UserProfileDetailView.as_view(), name="user-profile"),

    path("progress/me", ProgressMeView.as_view(), name="progress-me"),
    path("teacher/students/<int:student_id>/progress", TeacherStudentProgressView.as_view(), name="teacher-student-progress"),

    path("teacher/history", TeacherScoreHistoryView.as_view(), name="teacher-history"),
    path("teacher/groups", TeacherMyGroupsView.as_view(), name="teacher-groups"),
    path("teacher/groups/<int:group_id>/students", TeacherGroupStudentsView.as_view(), name="teacher-group-students"),
    path("teacher/students/<int:student_id>/deactivate", TeacherDeactivateStudentView.as_view(), name="teacher-student-deactivate"),
    path(
        "teacher/groups/<int:group_id>/students/<int:student_id>/score",
        TeacherScoreStudentView.as_view(),
        name="teacher-group-student-score",
    ),
    path("teacher/score", TeacherScoreStudentView.as_view(), name="teacher-score"),

    path("chat/ai/messages", AiChatMessagesView.as_view(), name="chat-ai-messages"),
    path("chat/friendly/conversations", FriendlyConversationsView.as_view(), name="chat-friendly-conversations"),
    path("chat/friendly/conversations/<int:conversation_id>/messages", FriendlyConversationMessagesView.as_view(), name="chat-friendly-messages"),

    path("grammar/topics", GrammarTopicsView.as_view(), name="grammar-topics"),

    path("support/tickets", SupportTicketListCreateView.as_view(), name="support-tickets"),
    path("support/tickets/<int:ticket_id>", SupportTicketUpdateView.as_view(), name="support-ticket-update"),
]
