from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from users.models import User
from groups.models import Group
from .serializers import RatingItemSerializer, MyRatingSerializer


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


def get_students_qs():
    return (
        User.objects.filter(role="student", is_iman_student=True, is_active=True)
        .select_related("group")
        .order_by("-points", "full_name")
    )


def get_place(user, qs):
    return qs.filter(
        Q(points__gt=user.points) |
        (Q(points=user.points) & Q(full_name__lt=user.full_name))
    ).count() + 1


class GlobalRatingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        students = list(get_students_qs())

        result = []
        for i, student in enumerate(students, start=1):
            student.place = i
            result.append(student)

        data = RatingItemSerializer(result, many=True).data
        return success_response("Iman students ratings", data)


class GroupRatingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        group = Group.objects.filter(id=group_id).first()
        if not group:
            return error_response("Group not found", {"group": ["not found"]}, 404)

        students = list(
            User.objects.filter(role="student", group_id=group_id, is_iman_student=True, is_active=True)
            .select_related("group")
            .order_by("-points", "full_name")
        )

        result = []
        for i, student in enumerate(students, start=1):
            student.place = i
            result.append(student)

        data = RatingItemSerializer(result, many=True).data

        return success_response(
            "Group ratings",
            {
                "group": {
                    "id": group.id,
                    "title": group.title,
                    "time": group.time,
                    "days_pattern": group.days_pattern,
                },
                "ratings": data,
            },
        )


class MyRatingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        if user.role != "student":
            return error_response("Only students", {"role": ["only students"]}, 403)

        global_qs = get_students_qs()
        global_place = get_place(user, global_qs)

        if user.group_id:
            group_qs = (
                User.objects.filter(role="student", group_id=user.group_id, is_iman_student=True, is_active=True)
                .select_related("group")
                .order_by("-points", "full_name")
            )
            group_place = get_place(user, group_qs)

            group_data = {
                "id": user.group.id,
                "title": user.group.title,
                "time": user.group.time,
                "days_pattern": user.group.days_pattern,
            }

            group_total = group_qs.count()
        else:
            group_place = None
            group_total = 0
            group_data = None

        data = {
            "student_id": user.id,
            "full_name": user.full_name,
            "points": user.points,
            "group": group_data,
            "places": {
                "global": {
                    "place": global_place,
                    "total": global_qs.count(),
                },
                "group": {
                    "place": group_place,
                    "total": group_total,
                },
            },
        }

        return success_response("My rating", MyRatingSerializer(data).data)
