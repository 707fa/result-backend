import base64
import binascii
import re
import uuid

from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import F
from django.core.files.base import ContentFile

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from groups.models import Group
from ratings.models import ScoreLog
from .models import User
from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    MeSerializer,
    AvatarUpdateSerializer,
    TeacherGroupSerializer,
    TeacherStudentSerializer,
    TeacherScoreStudentSerializer,
    TeacherScoreHistoryItemSerializer,
)


def success_response(message, data=None, status_code=status.HTTP_200_OK):
    return Response(
        {
            "success": True,
            "message": message,
            "data": data or {},
        },
        status=status_code,
    )


def error_response(message, errors=None, status_code=status.HTTP_400_BAD_REQUEST):
    return Response(
        {
            "success": False,
            "message": message,
            "errors": errors or {},
        },
        status=status_code,
    )


def build_auth_payload(user, refresh, request):
    me = MeSerializer(user, context={"request": request}).data
    return {
        "accessToken": str(refresh.access_token),
        "refreshToken": str(refresh),
        "token": str(refresh.access_token),
        "role": user.role,
        "userId": str(user.id),
        "user": me,
    }


def success_response_with_compat(message, payload, status_code=status.HTTP_200_OK):
    return Response(
        {
            "success": True,
            "message": message,
            "data": payload,
            **payload,
        },
        status=status_code,
    )


def normalize_register_payload(data):
    payload = data.copy()

    if "full_name" not in payload and "fullName" in payload:
        payload["full_name"] = payload.get("fullName")

    if "group_id" not in payload and "groupId" in payload:
        payload["group_id"] = payload.get("groupId")

    return payload


def avatar_url(request, user):
    if not getattr(user, "avatar", None):
        return None
    try:
        return request.build_absolute_uri(user.avatar.url)
    except Exception:
        return None


def to_front_group(group):
    return {
        "id": str(group.id),
        "title": group.title,
        "time": group.time,
        "daysPattern": group.days_pattern,
        "teacherId": str(group.teacher_id),
    }


def to_front_student(request, student):
    return {
        "id": str(student.id),
        "fullName": student.full_name,
        "phone": student.phone,
        "password": "",
        "groupId": str(student.group_id) if student.group_id else "",
        "avatarUrl": avatar_url(request, student),
        "points": float(student.points),
    }


def to_front_teacher(request, teacher, group_ids):
    return {
        "id": str(teacher.id),
        "fullName": teacher.full_name,
        "phone": teacher.phone,
        "password": "",
        "groupIds": [str(group_id) for group_id in group_ids],
        "avatarUrl": avatar_url(request, teacher),
    }


class RegisterView(APIView):

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=normalize_register_payload(request.data))
        if not serializer.is_valid():
            return error_response(
                "Validation error",
                serializer.errors,
                status.HTTP_400_BAD_REQUEST,
            )

        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        payload = build_auth_payload(user, refresh, request)
        return success_response_with_compat(
            "Registration successful",
            payload,
            status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Validation error",
                serializer.errors,
                status.HTTP_400_BAD_REQUEST,
            )

        phone = serializer.validated_data["phone"]
        password = serializer.validated_data["password"]

        user = authenticate(request, phone=phone, password=password)
        if not user:
            return error_response(
                "Invalid phone or password",
                {"credentials": ["Invalid phone or password"]},
                status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)

        payload = build_auth_payload(user, refresh, request)
        return success_response_with_compat(
            "Login successful",
            payload,
            status.HTTP_200_OK,
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = MeSerializer(request.user, context={"request": request}).data
        return success_response("Profile fetched successfully", data)


class UpdateAvatarView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        raw_avatar = request.data.get("avatarUrl")
        if isinstance(raw_avatar, str) and raw_avatar.startswith("data:"):
            match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", raw_avatar)
            if not match:
                return error_response(
                    "Validation error",
                    {"avatarUrl": ["Invalid base64 image format"]},
                    status.HTTP_400_BAD_REQUEST,
                )

            mime_type = match.group(1)
            encoded = match.group(2)
            extension = "png"
            if "/" in mime_type:
                extension = mime_type.split("/")[-1].lower().replace("+xml", "")
            if extension == "jpeg":
                extension = "jpg"

            try:
                decoded = base64.b64decode(encoded)
            except (binascii.Error, ValueError):
                return error_response(
                    "Validation error",
                    {"avatarUrl": ["Invalid base64 data"]},
                    status.HTTP_400_BAD_REQUEST,
                )

            filename = f"{uuid.uuid4().hex}.{extension}"
            request.user.avatar.save(filename, ContentFile(decoded), save=True)
            data = MeSerializer(request.user, context={"request": request}).data
            return success_response("Avatar updated successfully", data)

        serializer = AvatarUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return error_response(
                "Validation error",
                serializer.errors,
                status.HTTP_400_BAD_REQUEST,
            )

        serializer.save()
        data = MeSerializer(request.user, context={"request": request}).data
        return success_response("Avatar updated successfully", data)


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        return success_response("Logout successful", {})


class PlatformStateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        groups = list(
            Group.objects.select_related("teacher")
            .order_by("title", "time")
        )

        students = list(
            User.objects.filter(role="student")
            .select_related("group")
            .order_by("-points", "full_name")
        )

        teachers = list(
            User.objects.filter(role="teacher")
            .prefetch_related("teaching_groups")
            .order_by("full_name")
        )

        rankings = [
            {
                "studentId": str(student.id),
                "fullName": student.full_name,
                "groupId": str(student.group_id) if student.group_id else "",
                "points": float(student.points),
                "avatarUrl": avatar_url(request, student),
            }
            for student in students
        ]

        if request.user.role == "teacher":
            logs_qs = (
                ScoreLog.objects.filter(teacher=request.user)
                .select_related("teacher", "student", "group")
                .order_by("-created_at")
            )
        else:
            logs_qs = ScoreLog.objects.none()

        rating_logs = [
            {
                "id": str(log.id),
                "teacherId": str(log.teacher_id),
                "studentId": str(log.student_id),
                "groupId": str(log.group_id),
                "delta": log.delta,
                "label": "Points added" if log.delta >= 0 else "Points removed",
                "createdAt": log.created_at.isoformat(),
            }
            for log in logs_qs
        ]

        payload = {
            "students": [to_front_student(request, student) for student in students],
            "teachers": [
                to_front_teacher(
                    request,
                    teacher,
                    teacher.teaching_groups.values_list("id", flat=True),
                )
                for teacher in teachers
            ],
            "groups": [to_front_group(group) for group in groups],
            "rankings": rankings,
            "ratingLogs": rating_logs,
        }

        return success_response_with_compat("Platform state fetched successfully", payload)


class TeacherMyGroupsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != "teacher":
            return error_response(
                "Only teachers can access their groups",
                {"role": ["Only teachers can access their groups"]},
                status.HTTP_403_FORBIDDEN,
            )

        groups = (
            Group.objects.filter(teacher=request.user)
            .select_related("teacher")
            .prefetch_related("students")
            .order_by("title", "time")
        )

        data = TeacherGroupSerializer(groups, many=True).data
        return success_response("Teacher groups fetched successfully", data)


class TeacherGroupStudentsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        if request.user.role != "teacher":
            return error_response(
                "Only teachers can access group students",
                {"role": ["Only teachers can access group students"]},
                status.HTTP_403_FORBIDDEN,
            )

        group = Group.objects.filter(id=group_id, teacher=request.user).first()
        if not group:
            return error_response(
                "Group not found or access denied",
                {"group": ["Group not found or access denied"]},
                status.HTTP_404_NOT_FOUND,
            )

        students = (
            User.objects.filter(role="student", group=group)
            .select_related("group")
            .order_by("-points", "full_name")
        )

        data = {
            "group": {
                "id": group.id,
                "title": group.title,
                "time": group.time,
                "days_pattern": group.days_pattern,
            },
            "students": TeacherStudentSerializer(students, many=True).data,
        }

        return success_response("Group students fetched successfully", data)


class TeacherScoreStudentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id=None, student_id=None):
        if request.user.role != "teacher":
            return error_response(
                "Only teachers can score students",
                {"role": ["Only teachers can score students"]},
                status.HTTP_403_FORBIDDEN,
            )

        payload = request.data.copy()
        if student_id is not None:
            payload["student_id"] = student_id

        serializer = TeacherScoreStudentSerializer(data=payload)
        if not serializer.is_valid():
            return error_response(
                "Validation error",
                serializer.errors,
                status.HTTP_400_BAD_REQUEST,
            )

        student_id = serializer.validated_data["student_id"]
        delta = serializer.validated_data["delta"]

        student = (
            User.objects.select_related("group", "group__teacher")
            .filter(id=student_id, role="student")
            .first()
        )

        if not student:
            return error_response(
                "Student not found",
                {"student": ["Student not found"]},
                status.HTTP_404_NOT_FOUND,
            )

        if not student.group:
            return error_response(
                "Student has no group",
                {"group": ["Student has no group"]},
                status.HTTP_400_BAD_REQUEST,
            )

        if group_id is not None and student.group_id != group_id:
            return error_response(
                "Student does not belong to this group",
                {"group": ["Student does not belong to this group"]},
                status.HTTP_400_BAD_REQUEST,
            )

        if student.group.teacher_id != request.user.id:
            return error_response(
                "You cannot score students from another teacher group",
                {"group": ["You cannot score students from another teacher group"]},
                status.HTTP_403_FORBIDDEN,
            )

        with transaction.atomic():
            User.objects.filter(id=student.id).update(points=F("points") + delta)
            ScoreLog.objects.create(
                teacher=request.user,
                student=student,
                group=student.group,
                delta=delta,
            )
            student.refresh_from_db()

        data = {
            "student": {
                "id": student.id,
                "full_name": student.full_name,
                "points": student.points,
                "group_id": student.group.id,
                "group_title": student.group.title,
            },
            "delta": delta,
        }

        return success_response("Score updated successfully", data)

class TeacherScoreHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != "teacher":
            return error_response(
                "Only teachers can access score history",
                {"role": ["Only teachers can access score history"]},
                status.HTTP_403_FORBIDDEN,
            )

        group_id = request.query_params.get("group_id")
        student_id = request.query_params.get("student_id")

        logs = (
            ScoreLog.objects.filter(teacher=request.user)
            .select_related("teacher", "student", "group")
            .order_by("-created_at")
        )

        if group_id:
            logs = logs.filter(group_id=group_id)

        if student_id:
            logs = logs.filter(student_id=student_id)

        data = TeacherScoreHistoryItemSerializer(logs, many=True).data
        return success_response("Teacher score history fetched successfully", data)
