from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

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

    def test_register_accepts_frontend_group_title_payload(self):
        unique_group = Group.objects.create(
            title="Advanced",
            time="19:45",
            days_pattern="tts",
            teacher=self.teacher,
        )
        payload = {
            "fullName": "Title Student",
            "phone": "97 444-55-66",
            "password": "Pass12345!",
            "groupTitle": "Advanced",
            "time": "19:45",
            "daysPattern": "TTS",
        }
        response = self.client.post("/api/auth/register", payload, format="json")
        self.assertEqual(response.status_code, 201)
        created = User.objects.get(phone="+998974445566")
        self.assertEqual(created.group_id, unique_group.id)

    def test_login_accepts_phone_variants(self):
        response = self.client.post(
            "/api/auth/login",
            {"phone": "909000001", "password": "Pass12345!"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("role"), "teacher")

    def test_login_remember_me_extends_refresh_token_to_one_year(self):
        response = self.client.post(
            "/api/auth/login",
            {"phone": "909000001", "password": "Pass12345!", "remember_me": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        refresh = RefreshToken(response.data.get("refreshToken"))
        lifetime_seconds = int(refresh["exp"]) - int(refresh["iat"])
        self.assertGreaterEqual(lifetime_seconds, 365 * 24 * 60 * 60 - 60)

    def test_inactive_user_cannot_login(self):
        response = self.client.post(
            "/api/auth/login",
            {"phone": "909000003", "password": "Pass12345!"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_token_refresh_endpoint_uses_auth_refresh_throttle_scope(self):
        match = resolve("/api/token/refresh/")
        self.assertEqual(getattr(match.func.view_class, "throttle_scope", ""), "auth_refresh")

    @override_settings(DEBUG=False)
    @patch.dict(
        "os.environ",
        {"TELEGRAM_BOT_SECRET": "", "PAYMENT_WEBHOOK_SECRET": "", "ALLOW_INSECURE_WEBHOOKS": "true"},
    )
    def test_telegram_webhook_stays_locked_without_secret_in_production(self):
        response = self.client.post("/api/payments/webhook/telegram", {}, format="json")
        self.assertEqual(response.status_code, 401)

    @override_settings(DEBUG=False)
    def test_public_health_does_not_expose_provider_configuration(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertIn("database", data)
        self.assertNotIn("aiConfigured", data)
        self.assertNotIn("telegramConfigured", data)

    def test_admin_auth_accepts_username_field(self):
        user = authenticate(username="+998909000001", password="Pass12345!")
        self.assertEqual(user, self.teacher)
        self.assertTrue(user.is_staff)

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

    def test_free_student_can_load_unlocked_site_apis(self):
        self.auth("+998909000002", "Pass12345!")

        state_response = self.client.get("/api/platform/state")
        self.assertEqual(state_response.status_code, 200)
        payload = state_response.data["data"]
        self.assertFalse(payload["subscription"]["isPaid"])
        self.assertTrue(payload["teachers"])
        group_payload = next(item for item in payload["groups"] if item["id"] == str(self.group.id))
        self.assertEqual(group_payload["studentsCount"], 1)
        self.assertNotIn("Inactive Student", [item["fullName"] for item in payload["students"]])

        homework_response = self.client.get("/api/student/homework/tasks")
        self.assertEqual(homework_response.status_code, 200)

        friendly_response = self.client.get("/api/chat/friendly/conversations")
        self.assertEqual(friendly_response.status_code, 200)

    def test_platform_state_does_not_leak_other_students_payment_data(self):
        other_teacher = User.objects.create_user(
            full_name="Teacher Two",
            phone="+998909000007",
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
            full_name="Paid Other Student",
            phone="+998909000008",
            password="Pass12345!",
            role="student",
            group=other_group,
            is_paid=True,
        )

        self.auth("+998909000002", "Pass12345!")
        response = self.client.get("/api/platform/state")
        self.assertEqual(response.status_code, 200)
        students = response.data["data"]["students"]
        self.assertNotIn(str(other_student.id), {item["id"] for item in students})

        rankings = response.data["data"]["rankings"]
        public_other = next(item for item in rankings if item["studentId"] == str(other_student.id))
        self.assertNotIn("isPaid", public_other)
        self.assertNotIn("phone", public_other)

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
        self.assertIn("ai_progress_summary", user_admin.get_readonly_fields(request, self.student))
        teacher_field_names = [
            field
            for _, options in user_admin.get_fieldsets(request, self.student)
            for field in options["fields"]
        ]
        self.assertIn("ai_progress_summary", teacher_field_names)
        self.assertNotIn("progress_grammar", teacher_field_names)
        self.assertNotIn("progress_speaking", teacher_field_names)

    def test_group_title_uses_level_choices(self):
        choices = dict(Group._meta.get_field("title").choices)
        self.assertIn("Beginner", choices)
        self.assertIn("Elementary", choices)
        self.assertIn("Pre-Intermediate", choices)
