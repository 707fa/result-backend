from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import FileExtensionValidator


def _normalize_phone(value):
    phone = str(value or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())

    if len(digits) == 10 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 9:
        return f"+998{digits}"

    if digits.startswith("998") and len(digits) >= 12:
        return f"+998{digits[3:12]}"

    return phone


class UserManager(BaseUserManager):
    def create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError("Phone is required")
        phone = _normalize_phone(phone)
        extra_fields.setdefault("role", "student")
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
    avatar = models.FileField(
        upload_to="avatars/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_paid = models.BooleanField(default=False)
    paid_until = models.DateTimeField(blank=True, null=True)

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


class HomeworkTask(models.Model):
    TASK_TYPE_CHOICES = (
        ("homework", "Homework"),
        ("speaking", "Speaking"),
    )

    teacher = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="homework_tasks")
    group = models.ForeignKey("groups.Group", on_delete=models.CASCADE, related_name="homework_tasks")
    task_type = models.CharField(max_length=20, choices=TASK_TYPE_CHOICES, default="homework")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    speaking_topic = models.CharField(max_length=255, blank=True)
    speaking_level = models.CharField(max_length=40, blank=True)
    speaking_questions = models.JSONField(default=list, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Homework task #{self.id} ({self.group_id})"


class HomeworkSubmission(models.Model):
    STATUS_CHOICES = (
        ("submitted", "Submitted"),
        ("reviewed", "Reviewed"),
    )

    task = models.ForeignKey(HomeworkTask, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="homework_submissions")
    answer_text = models.TextField(max_length=4000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="submitted")
    teacher_comment = models.TextField(max_length=2000, blank=True)
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        unique_together = ("task", "student")

    def __str__(self):
        return f"Homework submission #{self.id} ({self.student_id} -> {self.task_id})"


class PaymentTransaction(models.Model):
    PROVIDER_CHOICES = (
        ("payme", "Payme"),
        ("click", "Click"),
        ("manual", "Manual"),
    )
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
    )

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="payment_transactions")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    external_id = models.CharField(max_length=255, blank=True, null=True)
    checkout_url = models.URLField(max_length=1500, blank=True)
    payload_raw = models.TextField(blank=True)
    manual_receipt = models.FileField(upload_to="payment_receipts/", blank=True, null=True)
    manual_receipt_uploaded_at = models.DateTimeField(blank=True, null=True)
    manual_verdict = models.CharField(max_length=24, default="pending")
    manual_verdict_reason = models.TextField(blank=True)
    manual_detected_amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    telegram_chat_id = models.CharField(max_length=64, blank=True)
    telegram_message_id = models.BigIntegerField(blank=True, null=True)
    reviewed_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_payment_transactions",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider} #{self.id} ({self.status})"

