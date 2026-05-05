from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from groups.models import Group


User = get_user_model()


class BackendSmokeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.factory = RequestFactory()

        self.teacher = User.objects.create_user(
            full_name="Teacher One",
            phone="+998909000001",
            password="Pass12345!",
            role="teacher",
        )

        self.group = Group.objects.create(
            title="Beginner",
            time="15:30",
            days_pattern="mwf",
            teacher=self.teacher,
        )

        self.student = User.objects.create_user(
            full_name="Student One",
            phone="+998909000002",
            password="Pass12345!",
            role="student",
            group=self.group,
        )

        self.inactive_student = User.objects.create_user(
            full_name="Inactive Student",
            phone="+998909000003",
            password="Pass12345!",
            role="student",
            group=self.group,
            is_iman_student=False,
            is_active=False,
        )

    def auth(self, phone, password):
        response = self.client.post(
            "/api/auth/login",
            {"phone": phone, "password": password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        token = response.data.get("token")
        self.assertTrue(token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_health_endpoint_public(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertIn("database", response.data["data"])

    def test_register_accepts_flexible_group_id_and_normalizes_phone(self):
        payload = {
            "fullName": "New Student",
            "phone": "97 111-22-33",
            "password": "Pass12345!",
            "groupId": f"group_{self.group.id}",
            "group": "Beginner",
            "time": "15:30",
            "days_pattern": "M/W/F",
        }
        response = self.client.post("/api/auth/register", payload, format="json")
        self.assertEqual(response.status_code, 201)
        created = User.objects.get(phone="+998971112233")
        self.assertEqual(created.group_id, self.group.id)
        self.assertEqual(created.role, "student")

    def test_login_accepts_phone_variants(self):
        response = self.client.post(
            "/api/auth/login",
            {"phone": "909000001", "password": "Pass12345!"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("role"), "teacher")

    def test_ai_chat_rejects_invalid_image_payload(self):
        self.student.is_paid = True
        self.student.save(update_fields=["is_paid"])
        self.auth("+998909000002", "Pass12345!")
        response = self.client.post(
            "/api/chat/ai/messages",
            {"text": "check this", "imageBase64": "bad-image"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data["errors"].get("imageBase64"))

    def test_ai_chat_requires_paid_subscription_for_student(self):
        self.auth("+998909000002", "Pass12345!")
        response = self.client.post(
            "/api/chat/ai/messages",
            {"text": "hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 402)

    def test_global_rating_excludes_inactive_students(self):
        self.auth("+998909000001", "Pass12345!")
        response = self.client.get("/api/ratings/global")
        self.assertEqual(response.status_code, 200)
        names = [item["full_name"] for item in response.data["data"]]
        self.assertIn("Student One", names)
        self.assertNotIn("Inactive Student", names)

    def test_teacher_admin_is_limited_to_own_groups_and_students(self):
        other_teacher = User.objects.create_user(
            full_name="Teacher Two",
            phone="+998909000004",
            password="Pass12345!",
            role="teacher",
        )
        other_group = Group.objects.create(
            title="Elementary",
            time="17:00",
            days_pattern="tts",
            teacher=other_teacher,
        )
        other_student = User.objects.create_user(
            full_name="Student Two",
            phone="+998909000005",
            password="Pass12345!",
            role="student",
            group=other_group,
        )
        new_registered = User.objects.create_user(
            full_name="New Registered",
            phone="+998909000006",
            password="Pass12345!",
            role="student",
            group=None,
            is_paid=True,
        )

        request = self.factory.get("/admin/")
        request.user = self.teacher

        group_admin = admin.site._registry[Group]
        user_admin = admin.site._registry[User]
        group_ids = set(group_admin.get_queryset(request).values_list("id", flat=True))
        user_ids = set(user_admin.get_queryset(request).values_list("id", flat=True))
        group_form_field = user_admin.formfield_for_foreignkey(User._meta.get_field("group"), request)

        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.is_staff)
        self.assertIn(self.group.id, group_ids)
        self.assertNotIn(other_group.id, group_ids)
        self.assertIn(self.student.id, user_ids)
        self.assertIn(new_registered.id, user_ids)
        self.assertNotIn(other_student.id, user_ids)
        self.assertTrue(user_admin.has_change_permission(request, new_registered))
        self.assertTrue(user_admin.has_delete_permission(request, new_registered))
        self.assertEqual(list(group_form_field.queryset), [self.group])

    def test_group_title_uses_level_choices(self):
        choices = dict(Group._meta.get_field("title").choices)
        self.assertIn("Beginner", choices)
        self.assertIn("Elementary", choices)
        self.assertIn("Pre-Intermediate", choices)
