from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager


class UserManager(BaseUserManager):
    def create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError("Phone is required")
        phone = phone.strip()
        user = self.model(phone=phone, username=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "teacher")

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(phone, password, **extra_fields)


class User(AbstractUser):
    ROLE_CHOICES = (
        ("student", "Student"),
        ("teacher", "Teacher"),
    )

    STATUS_CHOICES = (
        ("red", "Red"),
        ("yellow", "Yellow"),
        ("green", "Green"),
    )

    username = models.CharField(max_length=20, unique=True, blank=True, null=True)
    first_name = None
    last_name = None
    email = None

    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)
    avatar = models.FileField(upload_to="avatars/", blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )

    # Iman Students ranking isolation
    is_iman_student = models.BooleanField(default=True)

    # Study progress
    status_badge = models.CharField(max_length=10, choices=STATUS_CHOICES, default="yellow")
    progress_grammar = models.PositiveSmallIntegerField(default=0)
    progress_vocabulary = models.PositiveSmallIntegerField(default=0)
    progress_homework = models.PositiveSmallIntegerField(default=0)
    progress_speaking = models.PositiveSmallIntegerField(default=0)
    progress_attendance = models.PositiveSmallIntegerField(default=0)
    weekly_xp = models.IntegerField(default=0)
    level = models.PositiveIntegerField(default=1)
    streak_days = models.PositiveIntegerField(default=0)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]

    objects = UserManager()

    def __str__(self):
        return f"{self.full_name} ({self.phone})"


class GrammarTopic(models.Model):
    LEVEL_CHOICES = (
        ("beginner", "Beginner"),
        ("elementary", "Elementary"),
        ("pre-intermediate", "Pre-Intermediate"),
        ("intermediate", "Intermediate"),
        ("upper-intermediate", "Upper-Intermediate"),
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    level = models.CharField(max_length=40, choices=LEVEL_CHOICES, default="beginner")
    ppt_url = models.URLField(max_length=1024)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, related_name="grammar_topics")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class SupportTicket(models.Model):
    STATUS_CHOICES = (
        ("open", "Open"),
        ("in_progress", "In progress"),
        ("closed", "Closed"),
    )

    student = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="support_tickets")
    teacher = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="teacher_support_tickets")
    message = models.TextField(max_length=2000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Support #{self.id} ({self.status})"


class AiConversation(models.Model):
    user = models.OneToOneField("users.User", on_delete=models.CASCADE, related_name="ai_conversation")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"AI conversation #{self.id} ({self.user_id})"


class AiMessage(models.Model):
    ROLE_CHOICES = (
        ("user", "User"),
        ("assistant", "Assistant"),
    )

    conversation = models.ForeignKey(AiConversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    text = models.TextField(blank=True)
    image = models.FileField(upload_to="ai_homework/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"AI message #{self.id} ({self.role})"


class FriendlyConversation(models.Model):
    participants = models.ManyToManyField("users.User", related_name="friendly_conversations")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Friendly conversation #{self.id}"


class FriendlyMessage(models.Model):
    conversation = models.ForeignKey(FriendlyConversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="sent_friendly_messages")
    text = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Friendly message #{self.id} ({self.sender_id})"

