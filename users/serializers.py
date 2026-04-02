from rest_framework import serializers
from django.contrib.auth import get_user_model
from groups.models import Group
from ratings.models import ScoreLog
User = get_user_model()


# ========================
# AUTH
# ========================


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    group_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = User
        fields = ("full_name", "phone", "password", "group_id")

    def validate_group_id(self, value):
        if value is None:
            return value
        if not Group.objects.filter(id=value).exists():
            raise serializers.ValidationError("Group not found")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        group_id = validated_data.pop("group_id", None)
        group = Group.objects.filter(id=group_id).first() if group_id else None

        user = User.objects.create_user(
            full_name=validated_data["full_name"],
            phone=validated_data["phone"],
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
        )


class AvatarUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("avatar",)


# ========================
# TEACHER
# ========================

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
        )


class TeacherScoreStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    delta = serializers.IntegerField()
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
