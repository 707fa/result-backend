from rest_framework import serializers
import re

from django.contrib.auth import get_user_model
from groups.models import Group
from ratings.models import ScoreLog
from .models import (
    GrammarTopic,
    SupportTicket,
    AiConversation,
    AiMessage,
    FriendlyConversation,
    FriendlyMessage,
    HomeworkTask,
    HomeworkSubmission,
    PaymentTransaction,
)

User = get_user_model()


def _normalize_days_pattern(value):
    raw = str(value or "").strip().lower()
    compact = re.sub(r"[^a-zа-яё]", "", raw)

    if compact in {"mwf", "mondaywednesdayfriday", "понедельниксредапятница"}:
        return "mwf"
    if compact in {"tts", "tuesdaythursdaysaturday", "вторникчетвергсуббота"}:
        return "tts"

    if any(token in raw for token in ["m/w/f", "mon", "пн", "ср", "пт"]):
        return "mwf"
    if any(token in raw for token in ["t/t/s", "tue", "thu", "вт", "чт", "сб"]):
        return "tts"

    return ""


def _normalize_time(value):
    text = str(value or "").strip().replace(".", ":")
    if not text:
        return ""

    match = re.search(r"(\d{1,2})[:](\d{2})", text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"

    return text


def _normalize_group_title(value):
    return re.sub(r"[^a-z0-9а-яё]+", "", str(value or "").lower())


def _extract_group_id(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    match = re.search(r"(\d+)$", raw)
    if match:
        return int(match.group(1))
    return None


def _find_group_by_fields(raw_title, raw_time, raw_days):
    if not raw_title and not raw_time:
        return None

    normalized_days = _normalize_days_pattern(raw_days)
    normalized_time = _normalize_time(raw_time)
    normalized_title = _normalize_group_title(raw_title)

    queryset = Group.objects.all()
    if normalized_days in {"mwf", "tts"}:
        queryset = queryset.filter(days_pattern=normalized_days)
    if normalized_time:
        queryset = queryset.filter(time__startswith=normalized_time)

    if raw_title:
        direct = queryset.filter(title__iexact=raw_title.strip()).first()
        if direct:
            return direct

    for group in queryset:
        if normalized_title and _normalize_group_title(group.title) != normalized_title:
            continue
        if normalized_time and _normalize_time(group.time) != normalized_time:
            continue
        return group

    return None


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


def _phone_variants(value):
    normalized = _normalize_phone(value)
    digits = "".join(ch for ch in normalized if ch.isdigit())
    variants = []

    def add(item):
        item = str(item or "").strip()
        if item and item not in variants:
            variants.append(item)

    add(value)
    add(normalized)

    if digits:
        add(digits)
        if digits.startswith("998") and len(digits) >= 12:
            local = digits[3:12]
            add(local)
            add(f"+998{local}")

    return variants


class RegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=20)
    password = serializers.CharField(write_only=True, min_length=6)
    group_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    group = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    time = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    days_pattern = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_phone(self, value):
        phone = _normalize_phone(value)
        variants = _phone_variants(phone)

        if User.objects.filter(phone__in=variants).exists():
            raise serializers.ValidationError("User with this phone already exists")

        return phone

    def validate(self, attrs):
        group = None
        raw_group_id = attrs.get("group_id")
        raw_title = (attrs.get("group") or "").strip()
        raw_time = (attrs.get("time") or "").strip()
        raw_days = (attrs.get("days_pattern") or "").strip()

        if raw_group_id:
            parsed_id = _extract_group_id(raw_group_id)
            if parsed_id is not None:
                group = Group.objects.filter(id=parsed_id).first()
            if group is None:
                group = _find_group_by_fields(raw_title, raw_time, raw_days)
            if group is None:
                raise serializers.ValidationError({"group_id": ["Group not found"]})
        elif raw_title and raw_time:
            group = _find_group_by_fields(raw_title, raw_time, raw_days)
            if group is None:
                raise serializers.ValidationError({"group": ["Group not found for selected time/days"]})

        attrs["resolved_group"] = group
        return attrs

    def create(self, validated_data):
        group = validated_data.pop("resolved_group", None)
        password = validated_data["password"]
        full_name = validated_data["full_name"].strip()
        phone = _normalize_phone(validated_data["phone"])

        user = User.objects.create_user(
            full_name=full_name,
            phone=phone,
            password=password,
            role="student",
            group=group,
        )
        return user


class LoginSerializer(serializers.Serializer):
    phone = serializers.CharField()
    password = serializers.CharField()


class MeSerializer(serializers.ModelSerializer):
    group_title = serializers.CharField(source="group.title", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "full_name",
            "phone",
            "role",
            "points",
            "avatar",
            "group",
            "group_title",
            "is_paid",
            "paid_until",
            "status_badge",
            "progress_grammar",
            "progress_vocabulary",
            "progress_homework",
            "progress_speaking",
            "progress_attendance",
            "weekly_xp",
            "level",
            "streak_days",
        )


class AvatarUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("avatar",)


class TeacherGroupSerializer(serializers.ModelSerializer):
    teacher_id = serializers.IntegerField(source="teacher.id", read_only=True)
    students_count = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = (
            "id",
            "title",
            "time",
            "days_pattern",
            "teacher_id",
            "students_count",
        )

    def get_students_count(self, obj):
        return obj.students.filter(role="student").count()


class TeacherStudentSerializer(serializers.ModelSerializer):
    group_id = serializers.IntegerField(source="group.id", read_only=True)
    group_title = serializers.CharField(source="group.title", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "full_name",
            "phone",
            "points",
            "group_id",
            "group_title",
            "avatar",
            "is_paid",
            "paid_until",
            "status_badge",
            "progress_grammar",
            "progress_vocabulary",
            "progress_homework",
            "progress_speaking",
            "progress_attendance",
            "weekly_xp",
            "level",
            "streak_days",
        )


class TeacherScoreStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    delta = serializers.DecimalField(max_digits=7, decimal_places=2)
    label = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError("Delta cannot be 0")
        return value


class TeacherScoreHistoryItemSerializer(serializers.ModelSerializer):
    teacher_id = serializers.IntegerField(source="teacher.id", read_only=True)
    teacher_name = serializers.CharField(source="teacher.full_name", read_only=True)

    student_id = serializers.IntegerField(source="student.id", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)

    group_id = serializers.IntegerField(source="group.id", read_only=True)
    group_title = serializers.CharField(source="group.title", read_only=True)

    class Meta:
        model = ScoreLog
        fields = (
            "id",
            "teacher_id",
            "teacher_name",
            "student_id",
            "student_name",
            "group_id",
            "group_title",
            "delta",
            "created_at",
        )


class UserProfileSerializer(serializers.ModelSerializer):
    group_title = serializers.CharField(source="group.title", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "full_name",
            "phone",
            "role",
            "avatar",
            "group",
            "group_title",
            "points",
            "is_paid",
            "paid_until",
            "status_badge",
            "progress_grammar",
            "progress_vocabulary",
            "progress_homework",
            "progress_speaking",
            "progress_attendance",
            "weekly_xp",
            "level",
            "streak_days",
        )


class ProgressUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "progress_grammar",
            "progress_vocabulary",
            "progress_homework",
            "progress_speaking",
            "progress_attendance",
            "weekly_xp",
            "level",
            "streak_days",
            "status_badge",
        )
        extra_kwargs = {field: {"required": False} for field in fields}


class GrammarTopicSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True)

    class Meta:
        model = GrammarTopic
        fields = (
            "id",
            "title",
            "description",
            "level",
            "ppt_url",
            "is_active",
            "created_by",
            "created_by_name",
            "created_at",
        )
        read_only_fields = ("created_by",)


class SupportTicketSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    teacher_name = serializers.CharField(source="teacher.full_name", read_only=True)

    class Meta:
        model = SupportTicket
        fields = (
            "id",
            "student",
            "student_name",
            "teacher",
            "teacher_name",
            "message",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("student", "teacher", "status")


class SupportTicketUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportTicket
        fields = ("status",)


class AiMessageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = AiMessage
        fields = ("id", "role", "text", "image_url", "created_at")

    def get_image_url(self, obj):
        request = self.context.get("request")
        if not obj.image:
            return None
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url


class AiConversationSerializer(serializers.ModelSerializer):
    messages = AiMessageSerializer(many=True, read_only=True)

    class Meta:
        model = AiConversation
        fields = ("id", "user", "updated_at", "messages")


class AiSendMessageSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True)
    imageBase64 = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        text = (attrs.get("text") or "").strip()
        image = (attrs.get("imageBase64") or "").strip()
        if not text and not image:
            raise serializers.ValidationError({"message": "Provide text or image"})
        attrs["text"] = text
        attrs["imageBase64"] = image
        return attrs


class FriendlyMessageSerializer(serializers.ModelSerializer):
    sender_id = serializers.IntegerField(source="sender.id", read_only=True)
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)
    sender_role = serializers.CharField(source="sender.role", read_only=True)

    class Meta:
        model = FriendlyMessage
        fields = ("id", "sender_id", "sender_name", "sender_role", "text", "created_at")


class FriendlyConversationSerializer(serializers.ModelSerializer):
    messages = FriendlyMessageSerializer(many=True, read_only=True)

    class Meta:
        model = FriendlyConversation
        fields = ("id", "updated_at", "messages")


class FriendlyConversationCreateSerializer(serializers.Serializer):
    targetUserId = serializers.IntegerField()


class FriendlySendMessageSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2000)

    def validate_text(self, value):
        text = value.strip()
        if len(text) < 1:
            raise serializers.ValidationError("Message cannot be empty")
        return text


class HomeworkTaskSerializer(serializers.ModelSerializer):
    teacher_id = serializers.IntegerField(source="teacher.id", read_only=True)
    teacher_name = serializers.CharField(source="teacher.full_name", read_only=True)
    group_id = serializers.IntegerField(source="group.id", read_only=True)
    group_title = serializers.CharField(source="group.title", read_only=True)

    class Meta:
        model = HomeworkTask
        fields = (
            "id",
            "teacher_id",
            "teacher_name",
            "group_id",
            "group_title",
            "title",
            "description",
            "due_at",
            "is_active",
            "created_at",
        )


class HomeworkTaskCreateSerializer(serializers.Serializer):
    group_id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 3:
            raise serializers.ValidationError("Title is too short")
        return title


class HomeworkSubmissionSerializer(serializers.ModelSerializer):
    task_id = serializers.IntegerField(source="task.id", read_only=True)
    student_id = serializers.IntegerField(source="student.id", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_group_id = serializers.IntegerField(source="student.group.id", read_only=True)

    class Meta:
        model = HomeworkSubmission
        fields = (
            "id",
            "task_id",
            "student_id",
            "student_name",
            "student_group_id",
            "answer_text",
            "status",
            "teacher_comment",
            "score",
            "created_at",
            "updated_at",
        )


class HomeworkSubmissionCreateSerializer(serializers.Serializer):
    answer_text = serializers.CharField(max_length=4000)

    def validate_answer_text(self, value):
        text = value.strip()
        if len(text) < 2:
            raise serializers.ValidationError("Answer is too short")
        return text


class HomeworkSubmissionReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["submitted", "reviewed"], required=False)
    teacher_comment = serializers.CharField(max_length=2000, required=False, allow_blank=True, allow_null=True)
    score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)


class PaymentCreateSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=["payme", "click"])


class PaymentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTransaction
        fields = (
            "id",
            "provider",
            "amount",
            "status",
            "checkout_url",
            "created_at",
            "paid_at",
        )

