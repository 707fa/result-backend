from rest_framework import serializers
from django.contrib.auth import get_user_model
from groups.models import Group
from ratings.models import ScoreLog
from .models import GrammarTopic, SupportTicket, AiConversation, AiMessage

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=20)
    password = serializers.CharField(write_only=True, min_length=6)
    group_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    group = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    time = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    days_pattern = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_phone(self, value):
        phone = value.strip()
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError("User with this phone already exists")
        return phone

    def validate(self, attrs):
        group = None
        raw_group_id = (attrs.get("group_id") or "").strip()
        raw_title = (attrs.get("group") or "").strip()
        raw_time = (attrs.get("time") or "").strip()
        raw_days = (attrs.get("days_pattern") or "").strip().lower()

        if raw_group_id:
            if raw_group_id.isdigit():
                group = Group.objects.filter(id=int(raw_group_id)).first()
            if group is None and raw_title and raw_time:
                filters = {"title": raw_title, "time": raw_time}
                if raw_days in {"mwf", "tts"}:
                    filters["days_pattern"] = raw_days
                group = Group.objects.filter(**filters).first()
            if group is None:
                raise serializers.ValidationError({"group_id": ["Group not found"]})
        elif raw_title and raw_time:
            filters = {"title": raw_title, "time": raw_time}
            if raw_days in {"mwf", "tts"}:
                filters["days_pattern"] = raw_days
            group = Group.objects.filter(**filters).first()
            if group is None:
                raise serializers.ValidationError({"group": ["Group not found for selected time/days"]})

        attrs["resolved_group"] = group
        return attrs

    def create(self, validated_data):
        group = validated_data.pop("resolved_group", None)
        password = validated_data["password"]
        full_name = validated_data["full_name"].strip()
        phone = validated_data["phone"].strip()

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
