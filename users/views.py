import base64
import binascii
import hmac
import json
import logging
import os
import re
import uuid
import hashlib
from base64 import b64encode
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import Avg, Count, F
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import HttpResponse, StreamingHttpResponse

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from groups.models import Group
from ratings.models import ScoreLog
from .models import (
    User,
    GrammarTopic,
    SupportTicket,
    SupportTicketMessage,
    AiConversation,
    AiMessage,
    FriendlyConversation,
    FriendlyMessage,
    HomeworkTask,
    HomeworkSubmission,
    PaymentTransaction,
)
from .ai_service import generate_iman_ai_reply, generate_speaking_analysis
from .permissions import IsAuthenticatedAndPaid
from .subscription import get_subscription_payload, grant_subscription
from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    MeSerializer,
    AvatarUpdateSerializer,
    TeacherGroupSerializer,
    TeacherStudentSerializer,
    TeacherScoreStudentSerializer,
    TeacherScoreHistoryItemSerializer,
    UserProfileSerializer,
    ProgressUpdateSerializer,
    GrammarTopicSerializer,
    SupportTicketSerializer,
    SupportTicketMessageSerializer,
    SupportTicketUpdateSerializer,
    AiMessageSerializer,
    AiConversationSerializer,
    AiSendMessageSerializer,
    AiSpeakingCheckSerializer,
    FriendlyConversationSerializer,
    FriendlyConversationCreateSerializer,
    FriendlyMessageSerializer,
    FriendlySendMessageSerializer,
    HomeworkTaskSerializer,
    HomeworkTaskCreateSerializer,
    HomeworkSubmissionSerializer,
    HomeworkSubmissionCreateSerializer,
    HomeworkSubmissionReviewSerializer,
    PaymentCreateSerializer,
    ManualPaymentReceiptUploadSerializer,
    PaymentTransactionSerializer,
    TeacherGrantSubscriptionSerializer,
    TeacherPaymentDecisionSerializer,
    TeacherRenameGroupSerializer,
)


logger = logging.getLogger(__name__)

ALLOWED_AI_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
DEFAULT_AI_MAX_IMAGE_BYTES = 5 * 1024 * 1024
DEFAULT_VOICE_TTS_TIMEOUT_SECONDS = 65
DEFAULT_AVATAR_MAX_IMAGE_BYTES = 3 * 1024 * 1024
DEFAULT_VOICE_TTS_MAX_TEXT_CHARS = 700
DEFAULT_AI_CHAT_MAX_WORDS = 110


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
    subscription = get_subscription_payload(user)
    return {
        "accessToken": str(refresh.access_token),
        "refreshToken": str(refresh),
        "token": str(refresh.access_token),
        "role": user.role,
        "userId": str(user.id),
        "user": me,
        "subscription": subscription,
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


def get_env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def get_env_float(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _voice_timeout_seconds():
    return get_env_float("VOICE_TTS_TIMEOUT_SECONDS", DEFAULT_VOICE_TTS_TIMEOUT_SECONDS)


def _normalize_voice_format(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"mp3", "wav", "opus", "aac", "flac"}:
        return normalized
    return "mp3"


def _normalize_voice_name(value, provider):
    voice = str(value or "").strip()
    if voice:
        return voice
    if provider == "openai":
        return (os.environ.get("VOICE_TTS_OPENAI_VOICE", "") or "").strip() or "alloy"
    return (os.environ.get("VOICE_TTS_GEMINI_VOICE", "") or "").strip() or "Kore"


def _gemini_tts_request(text, lang, voice_name, audio_format):
    gemini_key = (os.environ.get("GEMINI_API_KEY", "") or "").strip()
    if not gemini_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    model = (os.environ.get("VOICE_TTS_GEMINI_MODEL", "") or "").strip() or "gemini-2.5-flash-preview-tts"
    timeout_seconds = _voice_timeout_seconds()
    request_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={quote(gemini_key)}"

    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name,
                    }
                }
            },
        },
    }

    if lang:
        payload["systemInstruction"] = {
            "parts": [{"text": f"Speak in language locale: {lang}"}]
        }

    req = Request(
        request_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def _resolve_ai_chat_provider_order():
    configured = str(os.environ.get("AI_CHAT_PROVIDER_ORDER", "") or "").strip().lower()
    if configured:
        result = []
        for item in configured.split(","):
            value = item.strip()
            if value in {"gemini", "openai"} and value not in result:
                result.append(value)
        if result:
            return result
    return ["gemini", "openai"]


def _resolve_ai_chat_max_words():
    return get_env_int("AI_CHAT_MAX_WORDS", DEFAULT_AI_CHAT_MAX_WORDS)


def _split_stream_chunks(text, chunk_size=18):
    content = str(text or "").strip()
    if not content:
        return []
    chunks = []
    index = 0
    step = max(6, int(chunk_size))
    while index < len(content):
        chunks.append(content[index : index + step])
        index += step
    return chunks


def _sse_event(event_name, payload):
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini TTS HTTP {exc.code}: {details[:300]}")
    except URLError as exc:
        raise RuntimeError(f"Gemini TTS network error: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Gemini TTS returned invalid JSON")

    candidate = (data.get("candidates") or [{}])[0]
    content = candidate.get("content") or {}
    parts = content.get("parts") or []
    inline_part = next((part.get("inlineData") for part in parts if isinstance(part, dict) and part.get("inlineData")), None)
    if not inline_part:
        raise RuntimeError("Gemini TTS did not return audio inlineData")

    audio_b64 = str(inline_part.get("data") or "").strip()
    if not audio_b64:
        raise RuntimeError("Gemini TTS returned empty audio data")

    mime_type = str(inline_part.get("mimeType") or "").strip() or (
        "audio/mpeg" if audio_format == "mp3" else "audio/wav"
    )
    try:
        audio_bytes = base64.b64decode(audio_b64, validate=True)
    except (binascii.Error, ValueError):
        raise RuntimeError("Gemini TTS returned invalid base64 audio")

    return audio_bytes, mime_type


def _openai_tts_request(text, voice_name, audio_format):
    openai_key = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    model = (os.environ.get("VOICE_TTS_OPENAI_MODEL", "") or "").strip() or "gpt-4o-mini-tts"
    timeout_seconds = _voice_timeout_seconds()
    request_url = "https://api.openai.com/v1/audio/speech"

    payload = {
        "model": model,
        "voice": voice_name,
        "input": text,
        "format": audio_format,
    }

    req = Request(
        request_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {openai_key}",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            audio_bytes = response.read()
            mime_type = response.headers.get_content_type() or "audio/mpeg"
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI TTS HTTP {exc.code}: {details[:300]}")
    except URLError as exc:
        raise RuntimeError(f"OpenAI TTS network error: {exc}")

    return audio_bytes, mime_type


def build_ai_unavailable_reply(text, has_image):
    if has_image and text:
        return (
            "AI service is temporarily unavailable. I saved your photo and message.\n"
            "Please try again in 1-2 minutes.\n"
            f"Your message: {text}"
        )
    if has_image:
        return (
            "AI service is temporarily unavailable. I received your photo. "
            "Please send a short text with what to check and try again in 1-2 minutes."
        )
    if text:
        return (
            "AI service is temporarily unavailable. "
            "Please try again in 1-2 minutes.\n"
            f"Your message: {text}"
        )
    return "AI service is temporarily unavailable. Send text or homework photo and try again."


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


def build_progress_block(student):
    return {
        "status": student.status_badge,
        "grammar": int(student.progress_grammar),
        "vocabulary": int(student.progress_vocabulary),
        "homework": int(student.progress_homework),
        "speaking": int(student.progress_speaking),
        "attendance": int(student.progress_attendance),
        "weeklyXp": int(student.weekly_xp),
        "level": int(student.level),
        "streakDays": int(student.streak_days),
    }


def _clamp_progress(value):
    return max(0, min(100, int(round(value))))


def refresh_student_progress_from_activity(student):
    """
    Backfill progress when legacy records have all zeros.
    Uses existing student activity so radar chart is meaningful.
    """
    current_values = [
        int(student.progress_grammar or 0),
        int(student.progress_vocabulary or 0),
        int(student.progress_homework or 0),
        int(student.progress_speaking or 0),
        int(student.progress_attendance or 0),
    ]
    if any(current_values):
        return

    base_qs = HomeworkSubmission.objects.filter(student=student)
    homework_qs = base_qs.filter(task__task_type="homework")
    speaking_qs = base_qs.filter(task__task_type="speaking")
    reviewed_homework_qs = homework_qs.filter(status="reviewed")
    reviewed_speaking_qs = speaking_qs.filter(status="reviewed")

    homework_total = homework_qs.count()
    homework_reviewed = reviewed_homework_qs.count()
    speaking_total = speaking_qs.count()
    speaking_reviewed = reviewed_speaking_qs.count()

    homework_score_avg = reviewed_homework_qs.aggregate(avg=Avg("score")).get("avg")
    speaking_score_avg = reviewed_speaking_qs.aggregate(avg=Avg("score")).get("avg")

    ai_activity = AiMessage.objects.filter(conversation__user=student, role="user").count()
    score_logs_count = ScoreLog.objects.filter(student=student).count()

    next_homework = 0
    next_speaking = 0
    next_grammar = 0
    next_vocabulary = 0
    next_attendance = 0

    if homework_total > 0:
        reviewed_ratio = (homework_reviewed / max(1, homework_total)) * 100
        score_signal = float(homework_score_avg) if homework_score_avg is not None else 0.0
        next_homework = _clamp_progress((reviewed_ratio * 0.65) + (score_signal * 0.35))

    if speaking_total > 0:
        speaking_ratio = (speaking_reviewed / max(1, speaking_total)) * 100
        speaking_signal = float(speaking_score_avg) if speaking_score_avg is not None else 0.0
        next_speaking = _clamp_progress((speaking_ratio * 0.45) + (speaking_signal * 0.55))

    if homework_score_avg is not None or speaking_score_avg is not None:
        grammar_parts = []
        if homework_score_avg is not None:
            grammar_parts.append(float(homework_score_avg))
        if speaking_score_avg is not None:
            grammar_parts.append(float(speaking_score_avg) * 0.85)
        next_grammar = _clamp_progress(sum(grammar_parts) / max(1, len(grammar_parts)))

    if ai_activity > 0:
        next_vocabulary = _clamp_progress(min(100, 20 + ai_activity * 4))
    elif homework_score_avg is not None:
        next_vocabulary = _clamp_progress(float(homework_score_avg) * 0.8)

    attendance_base = 8 * score_logs_count
    if homework_total > 0 or speaking_total > 0:
        attendance_base += 18
    next_attendance = _clamp_progress(min(100, attendance_base))

    updated = False
    if next_grammar > 0:
        student.progress_grammar = next_grammar
        updated = True
    if next_vocabulary > 0:
        student.progress_vocabulary = next_vocabulary
        updated = True
    if next_homework > 0:
        student.progress_homework = next_homework
        updated = True
    if next_speaking > 0:
        student.progress_speaking = next_speaking
        updated = True
    if next_attendance > 0:
        student.progress_attendance = next_attendance
        updated = True

    if updated:
        recalc_student_status(student)
        student.save(
            update_fields=[
                "progress_grammar",
                "progress_vocabulary",
                "progress_homework",
                "progress_speaking",
                "progress_attendance",
                "status_badge",
            ]
        )


def to_front_student(request, student, include_phone=True):
    return {
        "id": str(student.id),
        "fullName": student.full_name,
        "phone": student.phone if include_phone else "",
        "password": "",
        "groupId": str(student.group_id) if student.group_id else "",
        "avatarUrl": avatar_url(request, student),
        "points": float(student.points),
        "isPaid": bool(student.is_paid),
        "paidUntil": student.paid_until.isoformat() if student.paid_until else None,
        "progress": build_progress_block(student),
        "statusBadge": student.status_badge,
    }


def to_front_teacher(request, teacher, group_ids, include_phone=False):
    return {
        "id": str(teacher.id),
        "fullName": teacher.full_name,
        "phone": teacher.phone if include_phone else "",
        "password": "",
        "groupIds": [str(group_id) for group_id in group_ids],
        "avatarUrl": avatar_url(request, teacher),
    }


def recalc_student_status(student):
    values = [
        student.progress_grammar,
        student.progress_vocabulary,
        student.progress_homework,
        student.progress_speaking,
        student.progress_attendance,
    ]
    avg = sum(int(v) for v in values) / 5
    if avg >= 75:
        student.status_badge = "green"
    elif avg >= 45:
        student.status_badge = "yellow"
    else:
        student.status_badge = "red"


def smooth_progress(current_value, target_value, weight=0.2):
    current = int(current_value or 0)
    target = int(target_value or 0)
    next_value = round((current * (1.0 - weight)) + (target * weight))
    return max(0, min(100, next_value))


def update_student_progress_from_speaking(student, analysis, transcript):
    words_count = len([token for token in str(transcript or "").split() if token.strip()])
    score = int(analysis.get("score") or 0)
    grammar_score = int(analysis.get("grammarScore") or score)
    vocabulary_score = int(analysis.get("vocabularyScore") or score)
    fluency_score = int(analysis.get("fluencyScore") or score)

    student.progress_speaking = smooth_progress(student.progress_speaking, score, weight=0.24)
    student.progress_grammar = smooth_progress(student.progress_grammar, grammar_score, weight=0.14)
    student.progress_vocabulary = smooth_progress(student.progress_vocabulary, vocabulary_score, weight=0.14)
    student.progress_attendance = smooth_progress(student.progress_attendance, fluency_score, weight=0.1)

    xp_gain = max(2, min(15, score // 8 + max(0, words_count // 18)))
    student.weekly_xp = int(student.weekly_xp or 0) + xp_gain
    student.level = max(1, 1 + (student.weekly_xp // 120))
    student.streak_days = min(365, int(student.streak_days or 0) + 1)
    recalc_student_status(student)
    student.save(
        update_fields=[
            "progress_speaking",
            "progress_grammar",
            "progress_vocabulary",
            "progress_attendance",
            "weekly_xp",
            "level",
            "streak_days",
            "status_badge",
        ]
    )


def update_student_progress_from_ai_chat(student, user_text, has_image, assistant_reply):
    text_value = str(user_text or "").strip()
    reply_value = str(assistant_reply or "").strip().lower()
    words_count = len([token for token in text_value.split() if token.strip()])

    grammar_signal = 58
    vocabulary_signal = 56
    homework_signal = 54

    if has_image:
        homework_signal = 82
    if words_count >= 8:
        vocabulary_signal = 74
        grammar_signal = 70
    if words_count >= 18:
        vocabulary_signal = 80
        grammar_signal = 76

    if any(key in reply_value for key in ["mistake", "error", "corrected", "fix"]):
        grammar_signal += 4
    if any(key in reply_value for key in ["vocabulary", "word choice", "phrase"]):
        vocabulary_signal += 3
    if any(key in reply_value for key in ["homework", "task", "answer"]):
        homework_signal += 3

    student.progress_homework = smooth_progress(student.progress_homework, min(100, homework_signal), weight=0.16)
    student.progress_vocabulary = smooth_progress(student.progress_vocabulary, min(100, vocabulary_signal), weight=0.11)
    student.progress_grammar = smooth_progress(student.progress_grammar, min(100, grammar_signal), weight=0.11)

    xp_gain = 2
    if has_image:
        xp_gain += 3
    if words_count >= 8:
        xp_gain += 2
    if words_count >= 18:
        xp_gain += 2

    student.weekly_xp = int(student.weekly_xp or 0) + xp_gain
    student.level = max(1, 1 + (student.weekly_xp // 120))
    student.streak_days = min(365, int(student.streak_days or 0) + 1)
    recalc_student_status(student)
    student.save(
        update_fields=[
            "progress_homework",
            "progress_vocabulary",
            "progress_grammar",
            "weekly_xp",
            "level",
            "streak_days",
            "status_badge",
        ]
    )


def can_teacher_access_student(teacher, student):
    return (
        teacher.role == "teacher"
        and student.role == "student"
        and student.group_id is not None
        and student.group.teacher_id == teacher.id
    )


def can_user_chat_with_target(user, target):
    if user.id == target.id:
        return False

    if not target.is_active:
        return False

    if user.role == "teacher":
        return can_teacher_access_student(user, target)

    if user.role == "student":
        if target.role == "teacher":
            return bool(user.group_id and user.group and user.group.teacher_id == target.id)
        if target.role == "student":
            return bool(
                user.group_id
                and target.group_id
                and user.group_id == target.group_id
                and target.is_iman_student
            )

    return False


def get_or_create_direct_conversation(user, target):
    conversation = (
        FriendlyConversation.objects.filter(participants=user)
        .filter(participants=target)
        .annotate(participants_count=Count("participants"))
        .filter(participants_count=2)
        .first()
    )
    if conversation:
        return conversation

    conversation = FriendlyConversation.objects.create()
    conversation.participants.add(user, target)
    return conversation


def serialize_friendly_conversation_item(request, conversation):
    participants = list(conversation.participants.all())
    peer = next((participant for participant in participants if participant.id != request.user.id), None)
    last_message = conversation.messages.order_by("-created_at").first()

    return {
        "id": conversation.id,
        "updatedAt": conversation.updated_at.isoformat(),
        "peer": {
            "id": str(peer.id) if peer else "",
            "fullName": peer.full_name if peer else "",
            "role": peer.role if peer else "",
            "avatarUrl": avatar_url(request, peer) if peer else None,
        },
        "lastMessage": {
            "id": str(last_message.id),
            "text": last_message.text,
            "senderId": str(last_message.sender_id),
            "createdAt": last_message.created_at.isoformat(),
        }
        if last_message
        else None,
    }


def save_ai_image_from_data_url(image_base64, user):
    if not image_base64:
        return None

    raw_avatar = str(image_base64).strip()
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", raw_avatar)
    if not match:
        raise ValueError("Invalid image format. Use base64 data URL.")

    mime_type = match.group(1).lower()
    if mime_type not in ALLOWED_AI_IMAGE_MIME_TYPES:
        raise ValueError("Only JPG, PNG, and WEBP images are supported.")

    encoded = match.group(2)
    extension = "png"
    if "/" in mime_type:
        extension = mime_type.split("/")[-1].lower().replace("+xml", "")
    if extension == "jpeg":
        extension = "jpg"

    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Invalid image data.")

    max_bytes = get_env_int("AI_MAX_IMAGE_BYTES", DEFAULT_AI_MAX_IMAGE_BYTES)
    if len(decoded) > max_bytes:
        max_mb = round(max_bytes / (1024 * 1024), 2)
        raise ValueError(f"Image is too large. Max size is {max_mb} MB.")

    filename = f"{uuid.uuid4().hex}_{user.id}.{extension}"
    content = ContentFile(decoded)
    return filename, content


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"

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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

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
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        data = MeSerializer(request.user, context={"request": request}).data
        return success_response("Profile fetched successfully", data)


class UserProfileDetailView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request, user_id):
        target = get_object_or_404(User.objects.select_related("group", "group__teacher"), id=user_id)

        if request.user.role == "student" and request.user.id != target.id:
            return error_response("Access denied", {"profile": ["Students can access only own profile"]}, status.HTTP_403_FORBIDDEN)

        if request.user.role == "teacher":
            if target.role == "teacher" and request.user.id != target.id:
                return error_response("Access denied", {"profile": ["Teacher can access only own teacher profile"]}, status.HTTP_403_FORBIDDEN)
            if target.role == "student" and not can_teacher_access_student(request.user, target):
                return error_response("Access denied", {"profile": ["No access to this student"]}, status.HTTP_403_FORBIDDEN)

        data = UserProfileSerializer(target, context={"request": request}).data
        return success_response("Profile fetched successfully", data)


class ProgressMeView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        refresh_student_progress_from_activity(request.user)
        data = {
            "userId": request.user.id,
            "role": request.user.role,
            **build_progress_block(request.user),
        }
        return success_response("Progress fetched successfully", data)


class TeacherStudentProgressView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request, student_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can view student progress", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        student = get_object_or_404(User.objects.select_related("group", "group__teacher"), id=student_id, role="student")
        if not can_teacher_access_student(request.user, student):
            return error_response("Access denied", {"student": ["No access to this student"]}, status.HTTP_403_FORBIDDEN)
        refresh_student_progress_from_activity(student)

        data = {
            "userId": student.id,
            "fullName": student.full_name,
            "groupId": student.group_id,
            **build_progress_block(student),
        }
        return success_response("Student progress fetched successfully", data)

    def patch(self, request, student_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can update progress", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        student = get_object_or_404(User.objects.select_related("group", "group__teacher"), id=student_id, role="student")
        if not can_teacher_access_student(request.user, student):
            return error_response("Access denied", {"student": ["No access to this student"]}, status.HTTP_403_FORBIDDEN)

        serializer = ProgressUpdateSerializer(student, data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        serializer.save()
        recalc_student_status(student)
        student.save(update_fields=[
            "status_badge",
            "progress_grammar",
            "progress_vocabulary",
            "progress_homework",
            "progress_speaking",
            "progress_attendance",
            "weekly_xp",
            "level",
            "streak_days",
        ])

        data = {
            "userId": student.id,
            "fullName": student.full_name,
            "groupId": student.group_id,
            **build_progress_block(student),
        }
        return success_response("Student progress updated", data)


class UpdateAvatarView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

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

            mime_type = str(match.group(1) or "").lower()
            if mime_type not in ALLOWED_AI_IMAGE_MIME_TYPES:
                return error_response(
                    "Validation error",
                    {"avatarUrl": ["Only JPG, PNG, and WEBP images are supported"]},
                    status.HTTP_400_BAD_REQUEST,
                )

            encoded = match.group(2)
            extension = "png"
            if "/" in mime_type:
                extension = mime_type.split("/")[-1].lower().replace("+xml", "")
            if extension == "jpeg":
                extension = "jpg"

            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                return error_response(
                    "Validation error",
                    {"avatarUrl": ["Invalid base64 data"]},
                    status.HTTP_400_BAD_REQUEST,
                )

            if len(decoded) > DEFAULT_AVATAR_MAX_IMAGE_BYTES:
                return error_response(
                    "Validation error",
                    {"avatarUrl": ["Avatar image is too large (max 3MB)"]},
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


class HealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        db_ok = True
        try:
            User.objects.exists()
        except Exception:
            db_ok = False

        ai_provider = (os.environ.get("AI_PROVIDER", "gemini") or "gemini").strip().lower()
        ai_configured = bool(
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

        data = {
            "status": "ok" if db_ok else "degraded",
            "database": db_ok,
            "aiProvider": ai_provider,
            "aiConfigured": ai_configured,
            "telegramConfigured": bool(_telegram_bot_token() and _telegram_chat_ids()),
        }
        return success_response("Health check", data)


def get_subscription_price():
    raw = os.environ.get("SUBSCRIPTION_PRICE_UZS", "99000").strip()
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        value = Decimal("99000")
    value = max(value, Decimal("0"))
    return value.quantize(Decimal("0.01"))


def get_subscription_days():
    raw = str(os.environ.get("SUBSCRIPTION_DAYS", "30") or "30").strip()
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 30
    return max(1, min(days, 365))


def resolve_payment_return_url():
    candidate = (
        os.environ.get("PAYMENT_RETURN_URL")
        or os.environ.get("FRONTEND_BASE_URL")
        or "http://127.0.0.1:5188/student/subscription"
    )
    url = str(candidate or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return "http://127.0.0.1:5188/student/subscription"
    return url


def is_provider_configured(provider):
    provider_name = str(provider or "").strip().lower()
    if provider_name == "manual":
        return True
    if provider_name == "payme":
        return bool((os.environ.get("PAYME_MERCHANT_ID") or "").strip())
    if provider_name == "click":
        return bool((os.environ.get("CLICK_SERVICE_ID") or "").strip() and (os.environ.get("CLICK_MERCHANT_ID") or "").strip())
    return False


def parse_transaction_id(raw_value):
    raw = str(raw_value or "").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def parse_decimal_value(raw_value):
    if raw_value is None:
        return None
    try:
        text = str(raw_value).strip().replace(" ", "")
        if not text:
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return None


def amount_matches_transaction(transaction_amount, payload_amount):
    if payload_amount is None:
        return True

    tx_value = Decimal(transaction_amount).quantize(Decimal("0.01"))
    payload_value = Decimal(payload_amount).quantize(Decimal("0.01"))
    candidates = {payload_value}

    if payload_value == payload_value.to_integral_value():
        candidates.add((payload_value / Decimal("100")).quantize(Decimal("0.01")))

    return tx_value in candidates


def normalize_webhook_payload(payload):
    try:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        serialized = "{}"
    return serialized[:12000]


def build_payme_checkout_url(transaction, return_url):
    merchant_id = (os.environ.get("PAYME_MERCHANT_ID") or "").strip()
    if not merchant_id:
        return ""

    amount_tiyin = int((transaction.amount * 100).quantize(Decimal("1")))
    params = (
        f"m={merchant_id};"
        f"ac.user_id={transaction.user_id};"
        f"ac.tx={transaction.id};"
        f"a={amount_tiyin};"
        f"c={return_url};"
        f"ct=900000"
    )
    encoded = b64encode(params.encode("utf-8")).decode("ascii")
    return f"https://checkout.paycom.uz/{encoded}"


def build_click_checkout_url(transaction, return_url):
    service_id = (os.environ.get("CLICK_SERVICE_ID") or "").strip()
    merchant_id = (os.environ.get("CLICK_MERCHANT_ID") or "").strip()
    if not service_id or not merchant_id:
        return ""

    amount = str(transaction.amount.quantize(Decimal("0.01")))
    return (
        "https://my.click.uz/services/pay"
        f"?service_id={quote(service_id)}"
        f"&merchant_id={quote(merchant_id)}"
        f"&amount={quote(amount)}"
        f"&transaction_param={transaction.id}"
        f"&return_url={quote(return_url)}"
    )


def parse_payme_webhook_payload(payload):
    tx_id = None
    success = False
    external_id = None
    amount = None

    if isinstance(payload, dict):
        tx_id = payload.get("transaction_id")
        status_value = str(payload.get("status", "")).lower()
        success = status_value in {"success", "paid", "ok"}
        amount = parse_decimal_value(payload.get("amount"))
        result = payload.get("result")
        if isinstance(result, dict):
            amount = amount or parse_decimal_value(result.get("amount"))
            tx_id = tx_id or result.get("merchant_trans_id") or result.get("transaction_id")
            external_id = external_id or result.get("id")
            state = str(result.get("state", "")).lower()
            if state in {"2", "paid", "success"}:
                success = True
        params = payload.get("params")
        if isinstance(params, dict):
            state = str(params.get("state", "")).lower()
            if state in {"2", "paid", "success"}:
                success = True
            amount = amount or parse_decimal_value(params.get("amount"))
            account = params.get("account")
            if isinstance(account, dict):
                tx_id = tx_id or account.get("tx") or account.get("transaction_id")
        external_id = external_id or payload.get("id") or payload.get("payment_id")

    return parse_transaction_id(tx_id), success, str(external_id or "").strip(), amount


def parse_click_webhook_payload(payload):
    tx_id = None
    success = False
    external_id = None
    amount = None

    if isinstance(payload, dict):
        tx_id = payload.get("transaction_id") or payload.get("merchant_trans_id")
        error_code = str(payload.get("error", "")).strip()
        status_value = str(payload.get("status", "")).lower()
        success = error_code in {"0", ""} and status_value not in {"failed", "error"}
        if status_value in {"success", "paid", "completed"}:
            success = True
        amount = parse_decimal_value(payload.get("amount"))
        external_id = payload.get("click_trans_id") or payload.get("payment_id")

    return parse_transaction_id(tx_id), success, str(external_id or "").strip(), amount


def resolve_webhook_secret(request):
    token = (
        request.headers.get("X-Payment-Secret")
        or request.headers.get("X-Webhook-Secret")
        or request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    )
    return str(token or "").strip()


def is_valid_webhook_secret(request):
    configured = (os.environ.get("PAYMENT_WEBHOOK_SECRET") or "").strip()
    if not configured:
        allow_insecure = str(os.environ.get("ALLOW_INSECURE_WEBHOOKS", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        return bool(getattr(settings, "DEBUG", False) and allow_insecure)
    provided = resolve_webhook_secret(request)
    if not provided:
        return False
    return hmac.compare_digest(provided, configured)


def has_duplicate_external_id(provider, external_id, current_transaction_id):
    if not external_id:
        return False
    return PaymentTransaction.objects.filter(
        provider=provider,
        external_id=external_id,
    ).exclude(id=current_transaction_id).exists()


def _safe_json_loads(raw_text):
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        snippet = fenced.group(1).strip()
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        snippet = text[start : end + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def _receipt_amount_matches(tx_amount, detected_amount):
    if detected_amount is None:
        return False
    try:
        tx_value = Decimal(tx_amount).quantize(Decimal("0.01"))
        detected = Decimal(str(detected_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return False
    return tx_value == detected


def _build_manual_receipt_ai_prompt(payment_tx, receipt_url):
    return "\n".join(
        [
            "You validate a payment receipt for a language center.",
            "Return strict JSON only.",
            "{",
            '  "verdict": "likely_valid|likely_fake|unclear",',
            '  "detectedAmount": number | null,',
            '  "reason": "short reason",',
            '  "confidence": number',
            "}",
            f"Expected amount (UZS): {payment_tx.amount}",
            f"Student phone: {payment_tx.user.phone}",
            f"Receipt image URL: {receipt_url}",
            "Mark likely_fake if amount mismatch, obvious edit signs, or missing key details.",
        ]
    )


def evaluate_manual_receipt(payment_tx, receipt_url):
    prompt = _build_manual_receipt_ai_prompt(payment_tx, receipt_url)
    raw = generate_iman_ai_reply(
        text=prompt,
        level="intermediate",
        language="en",
        group_title=payment_tx.user.group.title if payment_tx.user.group_id else "",
        group_time=payment_tx.user.group.time if payment_tx.user.group_id else "",
    )
    parsed = _safe_json_loads(raw)
    if not parsed:
        return {
            "verdict": "pending",
            "reason": "AI check unavailable, pending teacher review.",
            "detected_amount": None,
            "raw": str(raw or "")[:1200],
        }

    verdict_raw = str(parsed.get("verdict") or "").strip().lower()
    if verdict_raw not in {"likely_valid", "likely_fake", "unclear"}:
        verdict_raw = "unclear"

    detected_amount = parse_decimal_value(parsed.get("detectedAmount"))
    reason = str(parsed.get("reason") or "").strip()[:500]

    if detected_amount is not None and _receipt_amount_matches(payment_tx.amount, detected_amount):
        verdict = "likely_valid" if verdict_raw != "likely_fake" else "likely_fake"
    else:
        verdict = "likely_fake" if detected_amount is not None else verdict_raw

    if verdict == "unclear":
        verdict = "pending"

    return {
        "verdict": verdict,
        "reason": reason or "Pending manual verification.",
        "detected_amount": detected_amount,
        "raw": str(raw or "")[:1200],
    }


def _telegram_bot_token():
    return str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def _telegram_chat_ids():
    raw = str(os.environ.get("TELEGRAM_CHAT_IDS") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not raw:
        return []
    values = []
    for chunk in re.split(r"[,\n;]+", raw):
        value = str(chunk or "").strip()
        if value:
            values.append(value)
    return values


def _telegram_sign_secret():
    return str(os.environ.get("TELEGRAM_BOT_SECRET") or os.environ.get("PAYMENT_WEBHOOK_SECRET") or "").strip()


def _telegram_sign(action, tx_id, days):
    secret = _telegram_sign_secret()
    if not secret:
        return None
    base = f"{action}:{tx_id}:{days}"
    digest = hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:16]


def _telegram_verify_sign(action, tx_id, days, sign):
    expected = _telegram_sign(action, tx_id, days)
    if not expected:
        return False
    return hmac.compare_digest(str(sign or ""), expected)


def is_valid_telegram_webhook_secret(request):
    configured = str(os.environ.get("TELEGRAM_BOT_SECRET") or os.environ.get("PAYMENT_WEBHOOK_SECRET") or "").strip()
    if not configured:
        allow_insecure = str(os.environ.get("ALLOW_INSECURE_WEBHOOKS", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        return settings.DEBUG or allow_insecure

    provided = (
        request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        or request.headers.get("X-Webhook-Secret")
        or request.headers.get("X-WEBHOOK-SECRET")
    )
    if not provided:
        return False
    return hmac.compare_digest(str(provided), configured)


def _telegram_api_call(method, payload):
    token = _telegram_bot_token()
    if not token:
        return None

    url = f"https://api.telegram.org/bot{token}/{method}"
    encoded = urlencode(payload).encode("utf-8")
    req = Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
        return _safe_json_loads(body) or {}
    except Exception:
        logger.exception("[PAYMENTS][TELEGRAM] %s failed", method)
        return None


def _build_telegram_caption(payment_tx):
    group = payment_tx.user.group
    group_label = f"{group.title} / {group.time}" if group else "No group"
    verdict = payment_tx.manual_verdict or "pending"
    verdict_label = {
        "likely_valid": "Likely valid",
        "likely_fake": "Likely fake",
        "pending": "Pending",
    }.get(verdict, verdict)
    return (
        f"New payment receipt #{payment_tx.id}\n"
        f"Student: {payment_tx.user.full_name}\n"
        f"Phone: {payment_tx.user.phone}\n"
        f"Group: {group_label}\n"
        f"Amount: {payment_tx.amount} UZS\n"
        f"AI verdict: {verdict_label}\n"
        f"Reason: {payment_tx.manual_verdict_reason or '-'}"
    )


def notify_telegram_manual_payment(payment_tx, request):
    token = _telegram_bot_token()
    chat_ids = _telegram_chat_ids()
    if not token or not chat_ids:
        return False

    receipt_url = None
    if payment_tx.manual_receipt:
        try:
            receipt_url = request.build_absolute_uri(payment_tx.manual_receipt.url)
        except Exception:
            receipt_url = None

    days = get_subscription_days()
    approve_sign = _telegram_sign("approve", payment_tx.id, days)
    reject_sign = _telegram_sign("reject", payment_tx.id, days)
    if not approve_sign or not reject_sign:
        logger.error("[PAYMENTS][TELEGRAM] callback signing secret is missing")
        return False
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "Approve access (30d)",
                    "callback_data": f"pay:approve:{payment_tx.id}:{days}:{approve_sign}",
                },
                {
                    "text": "Reject",
                    "callback_data": f"pay:reject:{payment_tx.id}:{days}:{reject_sign}",
                },
            ]
        ]
    }

    caption = _build_telegram_caption(payment_tx)
    if receipt_url:
        caption = f"{caption}\nReceipt: {receipt_url}"

    success = False
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": caption,
            "reply_markup": json.dumps(keyboard, separators=(",", ":")),
            "disable_web_page_preview": "false",
        }
        result = _telegram_api_call("sendMessage", payload)
        if result and result.get("ok"):
            success = True
            try:
                message = result.get("result") or {}
                payment_tx.telegram_chat_id = str(chat_id)
                payment_tx.telegram_message_id = message.get("message_id")
                payment_tx.save(update_fields=["telegram_chat_id", "telegram_message_id"])
            except Exception:
                logger.exception("[PAYMENTS][TELEGRAM] failed to store message id")
    return success


def _telegram_answer_callback(callback_query_id, text):
    if not callback_query_id:
        return
    _telegram_api_call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})


def _telegram_edit_message(chat_id, message_id, text):
    if not chat_id or not message_id:
        return
    _telegram_api_call(
        "editMessageText",
        {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": "true"},
    )


def _format_group_days(group):
    if not group:
        return "-"
    pattern = str(getattr(group, "days_pattern", "") or "").strip().lower()
    if pattern == "mwf":
        return "Mon/Wed/Fri"
    if pattern == "tts":
        return "Tue/Thu/Sat"
    return pattern or "-"


def _build_support_ticket_telegram_text(ticket):
    student = getattr(ticket, "student", None)
    group = getattr(student, "group", None) if student else None
    created_at = getattr(ticket, "created_at", None)
    created_at_text = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "-"
    teacher_name = getattr(getattr(ticket, "teacher", None), "full_name", "") or "-"
    return (
        f"🆘 New support request #{ticket.id}\n"
        f"Ticket ID: {ticket.id}\n"
        f"Student: {getattr(student, 'full_name', '-')}\n"
        f"Phone: {getattr(student, 'phone', '-')}\n"
        f"Level: {getattr(student, 'level', '-')}\n"
        f"Group: {getattr(group, 'title', '-') if group else '-'}\n"
        f"Time: {getattr(group, 'time', '-') if group else '-'}\n"
        f"Days: {_format_group_days(group)}\n"
        f"Teacher: {teacher_name}\n"
        f"Created: {created_at_text}\n"
        f"Problem:\n{getattr(ticket, 'message', '-')}"
    )


def notify_telegram_support_ticket(ticket):
    token = _telegram_bot_token()
    chat_ids = _telegram_chat_ids()
    if not token or not chat_ids:
        return False

    text = _build_support_ticket_telegram_text(ticket)
    success = False
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        result = _telegram_api_call("sendMessage", payload)
        if result and result.get("ok"):
            success = True
            message = result.get("result") or {}
            ticket.telegram_chat_id = str(chat_id)
            ticket.telegram_message_id = str(message.get("message_id") or "")
            ticket.save(update_fields=["telegram_chat_id", "telegram_message_id"])
    return success


def notify_telegram_support_message(ticket, message_text):
    token = _telegram_bot_token()
    chat_ids = _telegram_chat_ids()
    if not token or not chat_ids:
        return False

    student = getattr(ticket, "student", None)
    text = (
        f"💬 Support update for ticket #{ticket.id}\n"
        f"Ticket ID: {ticket.id}\n"
        f"Student: {getattr(student, 'full_name', '-')}\n"
        f"Phone: {getattr(student, 'phone', '-')}\n"
        f"Message:\n{str(message_text or '').strip()}"
    )

    success = False
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if ticket.telegram_message_id:
            payload["reply_to_message_id"] = str(ticket.telegram_message_id)
        result = _telegram_api_call("sendMessage", payload)
        if result and result.get("ok"):
            success = True
            message = result.get("result") or {}
            ticket.telegram_chat_id = str(chat_id)
            ticket.telegram_message_id = str(message.get("message_id") or ticket.telegram_message_id or "")
            ticket.save(update_fields=["telegram_chat_id", "telegram_message_id"])
    return success


def _extract_support_ticket_id_from_reply(reply_message):
    if not isinstance(reply_message, dict):
        return None
    text = str(reply_message.get("text") or "")
    if not text:
        return None
    # Preferred marker
    marker = re.search(r"Ticket ID:\s*(\d+)", text, flags=re.IGNORECASE)
    if marker:
        return int(marker.group(1))
    # Fallback for older messages
    fallback = re.search(r"support request #(\d+)", text, flags=re.IGNORECASE)
    if fallback:
        return int(fallback.group(1))
    return None


def _extract_support_ticket_id_from_text(value):
    text = str(value or "")
    if not text:
        return None
    marker = re.search(r"Ticket ID:\s*(\d+)", text, flags=re.IGNORECASE)
    if marker:
        return int(marker.group(1))
    fallback = re.search(r"(?:ticket|request|support)\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    if fallback:
        return int(fallback.group(1))
    return None


def _can_accept_telegram_support_reply(chat_id):
    allowed = {str(item) for item in _telegram_chat_ids()}
    return str(chat_id or "") in allowed


class PaymentCreateView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def post(self, request):
        if request.user.role != "student":
            return error_response("Only students can create payment", {"role": ["student only"]}, status.HTTP_403_FORBIDDEN)

        serializer = PaymentCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        provider = serializer.validated_data["provider"]
        amount = get_subscription_price()

        if amount <= 0:
            return error_response(
                "Subscription price is not configured",
                {"subscription": ["Set SUBSCRIPTION_PRICE_UZS in backend env"]},
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not is_provider_configured(provider):
            return error_response(
                "Payment provider is not configured",
                {"provider": ["Missing provider credentials in backend env"]},
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payment_tx = PaymentTransaction.objects.create(
            user=request.user,
            provider=provider,
            amount=amount,
            status="pending",
        )

        if provider == "manual":
            payment_tx.payload_raw = "manual_payment_request"
            payment_tx.save(update_fields=["payload_raw"])
            payload = {
                "transaction": PaymentTransactionSerializer(payment_tx, context={"request": request}).data,
                "subscription": get_subscription_payload(request.user),
                "manualFlow": True,
            }
            return success_response("Manual payment request created", payload, status.HTTP_201_CREATED)

        return_url = resolve_payment_return_url()

        checkout_url = (
            build_payme_checkout_url(payment_tx, return_url)
            if provider == "payme"
            else build_click_checkout_url(payment_tx, return_url)
        )

        if not checkout_url:
            payment_tx.status = "failed"
            payment_tx.payload_raw = "Payment provider config missing"
            payment_tx.save(update_fields=["status", "payload_raw"])
            return error_response(
                "Payment provider is not configured",
                {"provider": ["Missing provider config in env"]},
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payment_tx.checkout_url = checkout_url
        payment_tx.save(update_fields=["checkout_url"])

        payload = {
            "transaction": PaymentTransactionSerializer(payment_tx, context={"request": request}).data,
            "subscription": get_subscription_payload(request.user),
        }
        return success_response("Payment link created", payload, status.HTTP_201_CREATED)


class PaymentStatusView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        last_transaction = (
            PaymentTransaction.objects.filter(user=request.user)
            .order_by("-created_at")
            .first()
        )
        payload = {
            "subscription": get_subscription_payload(request.user),
            "lastTransaction": PaymentTransactionSerializer(last_transaction, context={"request": request}).data if last_transaction else None,
        }
        return success_response("Payment status", payload)


class PaymentManualReceiptUploadView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def post(self, request):
        if request.user.role != "student":
            return error_response("Only students can upload receipt", {"role": ["student only"]}, status.HTTP_403_FORBIDDEN)

        serializer = ManualPaymentReceiptUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        transaction_id = serializer.validated_data.get("transaction_id")
        amount = get_subscription_price()

        payment_tx = None
        if transaction_id:
            payment_tx = (
                PaymentTransaction.objects.select_related("user", "user__group")
                .filter(id=transaction_id, user=request.user, provider="manual")
                .first()
            )

        if not payment_tx:
            payment_tx = (
                PaymentTransaction.objects.select_related("user", "user__group")
                .filter(user=request.user, provider="manual", status="pending")
                .order_by("-created_at")
                .first()
            )

        if not payment_tx:
            payment_tx = PaymentTransaction.objects.create(
                user=request.user,
                provider="manual",
                amount=amount,
                status="pending",
            )

        receipt = serializer.validated_data["receipt"]
        payment_tx.manual_receipt = receipt
        payment_tx.manual_receipt_uploaded_at = timezone.now()
        payment_tx.manual_verdict = "pending"
        payment_tx.manual_verdict_reason = "Pending AI check."
        payment_tx.manual_detected_amount = None
        payment_tx.save(
            update_fields=[
                "manual_receipt",
                "manual_receipt_uploaded_at",
                "manual_verdict",
                "manual_verdict_reason",
                "manual_detected_amount",
            ]
        )

        receipt_url = request.build_absolute_uri(payment_tx.manual_receipt.url)
        ai_result = evaluate_manual_receipt(payment_tx, receipt_url)

        payment_tx.manual_verdict = ai_result["verdict"]
        payment_tx.manual_verdict_reason = ai_result["reason"]
        payment_tx.manual_detected_amount = ai_result["detected_amount"]
        payment_tx.payload_raw = normalize_webhook_payload(
            {
                "manual_receipt_ai": {
                    "verdict": ai_result["verdict"],
                    "reason": ai_result["reason"],
                    "detected_amount": str(ai_result["detected_amount"]) if ai_result["detected_amount"] is not None else None,
                    "raw": ai_result["raw"],
                }
            }
        )
        payment_tx.save(update_fields=["manual_verdict", "manual_verdict_reason", "manual_detected_amount", "payload_raw"])

        telegram_sent = notify_telegram_manual_payment(payment_tx, request)
        payload = {
            "transaction": PaymentTransactionSerializer(payment_tx, context={"request": request}).data,
            "subscription": get_subscription_payload(request.user),
            "telegramNotified": telegram_sent,
        }
        return success_response("Receipt uploaded", payload, status.HTTP_201_CREATED)


def serialize_teacher_payment_request_item(payment_tx, request=None):
    student = payment_tx.user
    group = getattr(student, "group", None)
    return {
        "transaction": PaymentTransactionSerializer(payment_tx, context={"request": request}).data,
        "student": {
            "id": student.id,
            "fullName": student.full_name,
            "phone": student.phone,
            "groupId": group.id if group else None,
            "groupTitle": group.title if group else None,
            "groupTime": group.time if group else None,
        },
    }


class TeacherPaymentRequestsView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        if request.user.role != "teacher":
            return error_response("Only teachers can view payment requests", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        qs = (
            PaymentTransaction.objects.select_related("user", "user__group")
            .filter(provider="manual", user__role="student", user__group__teacher=request.user, status="pending")
            .order_by("-created_at")
        )
        payload = {
            "requests": [serialize_teacher_payment_request_item(item, request=request) for item in qs],
        }
        return success_response("Teacher payment requests", payload)


class TeacherPaymentRequestApproveView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def post(self, request, transaction_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can approve payment requests", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        serializer = TeacherPaymentDecisionSerializer(data=request.data or {})
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        days = serializer.validated_data.get("days", get_subscription_days())

        with transaction.atomic():
            payment_tx = (
                PaymentTransaction.objects.select_for_update()
                .select_related("user", "user__group")
                .filter(
                    id=transaction_id,
                    provider="manual",
                    status="pending",
                    user__role="student",
                    user__group__teacher=request.user,
                )
                .first()
            )
            if not payment_tx:
                return error_response(
                    "Payment request not found",
                    {"transaction": ["Payment request not found or already processed"]},
                    status.HTTP_404_NOT_FOUND,
                )

            payment_tx.status = "paid"
            payment_tx.paid_at = timezone.now()
            payment_tx.reviewed_by = request.user
            payment_tx.reviewed_at = timezone.now()
            payment_tx.manual_verdict_reason = payment_tx.manual_verdict_reason or "Approved by teacher."
            payment_tx.save(update_fields=["status", "paid_at", "reviewed_by", "reviewed_at", "manual_verdict_reason"])
            paid_until = grant_subscription(payment_tx.user, days=days)

        payload = {
            "request": serialize_teacher_payment_request_item(payment_tx, request=request),
            "paidUntil": paid_until.isoformat(),
        }
        return success_response("Payment request approved", payload)


class TeacherPaymentRequestRejectView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def post(self, request, transaction_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can reject payment requests", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            payment_tx = (
                PaymentTransaction.objects.select_for_update()
                .select_related("user", "user__group")
                .filter(
                    id=transaction_id,
                    provider="manual",
                    status="pending",
                    user__role="student",
                    user__group__teacher=request.user,
                )
                .first()
            )
            if not payment_tx:
                return error_response(
                    "Payment request not found",
                    {"transaction": ["Payment request not found or already processed"]},
                    status.HTTP_404_NOT_FOUND,
                )

            payment_tx.status = "failed"
            payment_tx.reviewed_by = request.user
            payment_tx.reviewed_at = timezone.now()
            payment_tx.manual_verdict_reason = payment_tx.manual_verdict_reason or "Rejected by teacher."
            payment_tx.save(update_fields=["status", "reviewed_by", "reviewed_at", "manual_verdict_reason"])

        payload = {"request": serialize_teacher_payment_request_item(payment_tx, request=request)}
        return success_response("Payment request rejected", payload)


class PaymentTelegramWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if not is_valid_telegram_webhook_secret(request):
            return error_response("Unauthorized", {"secret": ["Invalid webhook secret"]}, status.HTTP_401_UNAUTHORIZED)

        update = request.data if isinstance(request.data, dict) else {}
        message_update = update.get("message") if isinstance(update, dict) else None
        if isinstance(message_update, dict):
            chat = message_update.get("chat") if isinstance(message_update.get("chat"), dict) else {}
            chat_id = str(chat.get("id") or "")
            if _can_accept_telegram_support_reply(chat_id):
                replied_to = message_update.get("reply_to_message") if isinstance(message_update.get("reply_to_message"), dict) else None
                reply_to_message_id = str((replied_to or {}).get("message_id") or "").strip()
                reply_text = str(message_update.get("text") or message_update.get("caption") or "").strip()
                if reply_text.startswith("/"):
                    return success_response("Ignored", {"ok": True})

                ticket_id = None
                if reply_to_message_id:
                    by_message_id = SupportTicket.objects.filter(
                        telegram_chat_id=chat_id,
                        telegram_message_id=reply_to_message_id,
                    ).first()
                    if by_message_id:
                        ticket_id = by_message_id.id

                if not ticket_id:
                    ticket_id = _extract_support_ticket_id_from_reply(replied_to)

                if not ticket_id:
                    ticket_id = _extract_support_ticket_id_from_text(reply_text)
                if ticket_id and reply_text:
                    ticket = SupportTicket.objects.filter(id=ticket_id).select_related("teacher").first()
                    if ticket and (not ticket.telegram_chat_id or str(ticket.telegram_chat_id) == chat_id):
                        now = timezone.now()
                        reply_body = reply_text[:2000]
                        SupportTicketMessage.objects.create(
                            ticket=ticket,
                            sender_type="support",
                            text=reply_body,
                            source="telegram",
                            read_by_support_at=now,
                        )

                        SupportTicketMessage.objects.filter(
                            ticket=ticket,
                            sender_type="student",
                            read_by_support_at__isnull=True,
                        ).update(read_by_support_at=now)

                        update_fields = ["teacher_reply", "teacher_reply_at", "updated_at"]
                        ticket.teacher_reply = reply_body
                        ticket.teacher_reply_at = now
                        ticket.telegram_chat_id = chat_id
                        update_fields.append("telegram_chat_id")
                        incoming_message_id = str(message_update.get("message_id") or "").strip()
                        if incoming_message_id:
                            ticket.telegram_message_id = incoming_message_id
                            update_fields.append("telegram_message_id")
                        if ticket.status == "open":
                            ticket.status = "in_progress"
                            update_fields.append("status")
                        ticket.save(update_fields=update_fields)
                        return success_response("Support reply synced", {"ticketId": ticket.id, "synced": True})

        callback_query = update.get("callback_query") if isinstance(update, dict) else None
        if not isinstance(callback_query, dict):
            return success_response("Ignored", {"ok": True})

        callback_id = callback_query.get("id")
        data = str(callback_query.get("data") or "").strip()
        message = callback_query.get("message") if isinstance(callback_query.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or "")
        message_id = message.get("message_id")

        chunks = data.split(":")
        if len(chunks) != 5 or chunks[0] != "pay":
            _telegram_answer_callback(callback_id, "Invalid action")
            return success_response("Ignored", {"ok": True})

        action = chunks[1]
        tx_id = parse_transaction_id(chunks[2])
        days = int(chunks[3]) if chunks[3].isdigit() else get_subscription_days()
        sign = chunks[4]

        if action not in {"approve", "reject"} or tx_id is None:
            _telegram_answer_callback(callback_id, "Invalid action payload")
            return success_response("Ignored", {"ok": True})

        if not _telegram_verify_sign(action, tx_id, days, sign):
            _telegram_answer_callback(callback_id, "Signature check failed")
            return error_response("Unauthorized", {"signature": ["Invalid callback signature"]}, status.HTTP_401_UNAUTHORIZED)

        with transaction.atomic():
            payment_tx = (
                PaymentTransaction.objects.select_for_update()
                .select_related("user")
                .filter(id=tx_id, provider="manual")
                .first()
            )
            if not payment_tx:
                _telegram_answer_callback(callback_id, "Transaction not found")
                return error_response("Not found", {"transaction": ["Not found"]}, status.HTTP_404_NOT_FOUND)

            if payment_tx.status == "paid":
                _telegram_answer_callback(callback_id, "Already approved")
            else:
                if action == "approve":
                    payment_tx.status = "paid"
                    payment_tx.paid_at = payment_tx.paid_at or timezone.now()
                    payment_tx.reviewed_at = timezone.now()
                    payment_tx.manual_verdict_reason = payment_tx.manual_verdict_reason or "Approved from Telegram."
                    payment_tx.save(update_fields=["status", "paid_at", "reviewed_at", "manual_verdict_reason"])
                    grant_subscription(payment_tx.user, days=days)
                    _telegram_answer_callback(callback_id, "Approved")
                else:
                    payment_tx.status = "failed"
                    payment_tx.reviewed_at = timezone.now()
                    payment_tx.manual_verdict_reason = payment_tx.manual_verdict_reason or "Rejected from Telegram."
                    payment_tx.save(update_fields=["status", "reviewed_at", "manual_verdict_reason"])
                    _telegram_answer_callback(callback_id, "Rejected")

        status_label = "APPROVED" if payment_tx.status == "paid" else "REJECTED"
        summary = (
            f"Payment #{payment_tx.id} {status_label}\n"
            f"Student: {payment_tx.user.full_name}\n"
            f"Amount: {payment_tx.amount} UZS\n"
            f"Status: {payment_tx.status}"
        )
        _telegram_edit_message(chat_id, message_id, summary)
        return success_response("Telegram callback processed", {"transactionId": payment_tx.id, "status": payment_tx.status})


class PaymentWebhookPaymeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if not is_valid_webhook_secret(request):
            return error_response("Unauthorized", {"secret": ["Invalid webhook secret"]}, status.HTTP_401_UNAUTHORIZED)

        payload = request.data if isinstance(request.data, dict) else {}
        tx_id, success, external_id, webhook_amount = parse_payme_webhook_payload(payload)
        if not tx_id:
            return error_response("Invalid payload", {"transaction": ["Missing transaction id"]}, status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            payment_tx = (
                PaymentTransaction.objects.select_for_update()
                .select_related("user")
                .filter(id=tx_id, provider="payme")
                .first()
            )
            if not payment_tx:
                return error_response("Transaction not found", {"transaction": ["not found"]}, status.HTTP_404_NOT_FOUND)

            if has_duplicate_external_id("payme", external_id, payment_tx.id):
                return error_response(
                    "Duplicate external payment id",
                    {"payment": ["This payment id is already used"]},
                    status.HTTP_409_CONFLICT,
                )

            payload_raw = normalize_webhook_payload(payload)
            payment_tx.external_id = external_id or payment_tx.external_id
            payment_tx.payload_raw = payload_raw

            if success and not amount_matches_transaction(payment_tx.amount, webhook_amount):
                if payment_tx.status != "paid":
                    payment_tx.status = "failed"
                payment_tx.save(update_fields=["status", "external_id", "payload_raw"])
                return error_response(
                    "Amount mismatch",
                    {"payment": ["Webhook amount does not match transaction amount"]},
                    status.HTTP_400_BAD_REQUEST,
                )

            if success:
                was_paid = payment_tx.status == "paid"
                payment_tx.status = "paid"
                payment_tx.paid_at = payment_tx.paid_at or timezone.now()
                payment_tx.save(update_fields=["status", "paid_at", "external_id", "payload_raw"])
                if not was_paid:
                    grant_subscription(payment_tx.user, days=get_subscription_days())
            else:
                if payment_tx.status != "paid":
                    payment_tx.status = "failed"
                payment_tx.save(update_fields=["status", "external_id", "payload_raw"])

        return success_response("Webhook accepted", {"transactionId": payment_tx.id, "status": payment_tx.status})


class PaymentWebhookClickView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if not is_valid_webhook_secret(request):
            return error_response("Unauthorized", {"secret": ["Invalid webhook secret"]}, status.HTTP_401_UNAUTHORIZED)

        payload = request.data if isinstance(request.data, dict) else {}
        tx_id, success, external_id, webhook_amount = parse_click_webhook_payload(payload)
        if not tx_id:
            return error_response("Invalid payload", {"transaction": ["Missing transaction id"]}, status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            payment_tx = (
                PaymentTransaction.objects.select_for_update()
                .select_related("user")
                .filter(id=tx_id, provider="click")
                .first()
            )
            if not payment_tx:
                return error_response("Transaction not found", {"transaction": ["not found"]}, status.HTTP_404_NOT_FOUND)

            if has_duplicate_external_id("click", external_id, payment_tx.id):
                return error_response(
                    "Duplicate external payment id",
                    {"payment": ["This payment id is already used"]},
                    status.HTTP_409_CONFLICT,
                )

            payload_raw = normalize_webhook_payload(payload)
            payment_tx.external_id = external_id or payment_tx.external_id
            payment_tx.payload_raw = payload_raw

            if success and not amount_matches_transaction(payment_tx.amount, webhook_amount):
                if payment_tx.status != "paid":
                    payment_tx.status = "failed"
                payment_tx.save(update_fields=["status", "external_id", "payload_raw"])
                return error_response(
                    "Amount mismatch",
                    {"payment": ["Webhook amount does not match transaction amount"]},
                    status.HTTP_400_BAD_REQUEST,
                )

            if success:
                was_paid = payment_tx.status == "paid"
                payment_tx.status = "paid"
                payment_tx.paid_at = payment_tx.paid_at or timezone.now()
                payment_tx.save(update_fields=["status", "paid_at", "external_id", "payload_raw"])
                if not was_paid:
                    grant_subscription(payment_tx.user, days=get_subscription_days())
            else:
                if payment_tx.status != "paid":
                    payment_tx.status = "failed"
                payment_tx.save(update_fields=["status", "external_id", "payload_raw"])

        return success_response("Webhook accepted", {"transactionId": payment_tx.id, "status": payment_tx.status})


class PlatformStateView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        subscription = get_subscription_payload(request.user)
        paid_access = bool(subscription.get("isPaid")) or request.user.role != "student"

        groups = list(
            Group.objects.select_related("teacher")
            .order_by("title", "time")
        )

        students = list(
            User.objects.filter(role="student", is_iman_student=True, is_active=True)
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
                "statusBadge": student.status_badge,
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
                "delta": float(log.delta),
                "label": "Points added" if log.delta >= 0 else "Points removed",
                "createdAt": log.created_at.isoformat(),
            }
            for log in logs_qs
        ]

        payload_students = []
        for student in students:
            include_phone = request.user.role == "teacher" or request.user.id == student.id
            payload_students.append(to_front_student(request, student, include_phone=include_phone))

        payload = {
            "students": payload_students,
            "teachers": [
                to_front_teacher(
                    request,
                    teacher,
                    teacher.teaching_groups.values_list("id", flat=True),
                    include_phone=request.user.role == "teacher",
                )
                for teacher in teachers
            ]
            if paid_access or request.user.role == "teacher"
            else [],
            "groups": [to_front_group(group) for group in groups],
            "rankings": rankings,
            "ratingLogs": rating_logs if paid_access or request.user.role == "teacher" else [],
            "subscription": subscription,
        }

        return success_response_with_compat("Platform state fetched successfully", payload)


class TeacherDeactivateStudentView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def patch(self, request, student_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can deactivate students", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        student = get_object_or_404(User.objects.select_related("group", "group__teacher"), id=student_id, role="student")
        if not can_teacher_access_student(request.user, student):
            return error_response("Access denied", {"student": ["No access to this student"]}, status.HTTP_403_FORBIDDEN)

        student.is_iman_student = False
        student.is_active = False
        student.group = None
        student.save(update_fields=["is_iman_student", "is_active", "group"])

        payload = {
            "studentId": student.id,
            "isImanStudent": student.is_iman_student,
            "isActive": student.is_active,
        }
        return success_response("Student deactivated", payload)


class TeacherGrantStudentSubscriptionView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def post(self, request, student_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can grant access", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        student = get_object_or_404(User.objects.select_related("group", "group__teacher"), id=student_id, role="student")
        if not can_teacher_access_student(request.user, student):
            return error_response("Access denied", {"student": ["No access to this student"]}, status.HTTP_403_FORBIDDEN)

        serializer = TeacherGrantSubscriptionSerializer(data=request.data or {})
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        days = serializer.validated_data.get("days", get_subscription_days())
        paid_until = grant_subscription(student, days=days)

        payload = {
            "studentId": student.id,
            "fullName": student.full_name,
            "isPaid": True,
            "paidUntil": paid_until.isoformat(),
            "daysGranted": days,
        }
        return success_response("Student access granted", payload)


class TeacherMyGroupsView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

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


class TeacherGroupUpdateView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def patch(self, request, group_id):
        if request.user.role != "teacher":
            return error_response(
                "Only teachers can update group title",
                {"role": ["Only teachers can update group title"]},
                status.HTTP_403_FORBIDDEN,
            )

        group = Group.objects.filter(id=group_id, teacher=request.user).first()
        if not group:
            return error_response(
                "Group not found or access denied",
                {"group": ["Group not found or access denied"]},
                status.HTTP_404_NOT_FOUND,
            )

        serializer = TeacherRenameGroupSerializer(data=request.data or {})
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        next_title = serializer.validated_data["title"]
        if group.title != next_title:
            group.title = next_title
            group.save(update_fields=["title"])

        payload = {
            "group": {
                "id": group.id,
                "title": group.title,
                "time": group.time,
                "days_pattern": group.days_pattern,
            }
        }
        return success_response("Group title updated", payload)


class TeacherGroupStudentsView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

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
            User.objects.filter(role="student", group=group, is_iman_student=True, is_active=True)
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
    permission_classes = [IsAuthenticatedAndPaid]

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

        if not student.is_active or not student.is_iman_student:
            return error_response(
                "Student is inactive",
                {"student": ["Inactive students cannot be scored"]},
                status.HTTP_400_BAD_REQUEST,
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
            User.objects.filter(id=student.id).update(points=F("points") + Decimal(delta))
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
                "points": float(student.points),
                "group_id": student.group.id,
                "group_title": student.group.title,
            },
            "delta": float(delta),
        }

        return success_response("Score updated successfully", data)


class TeacherScoreHistoryView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

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


class TeacherHomeworkTasksView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        if request.user.role != "teacher":
            return error_response("Only teachers can access homework tasks", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        tasks = (
            HomeworkTask.objects.filter(teacher=request.user, is_active=True, task_type="homework")
            .select_related("teacher", "group")
            .order_by("-created_at")
        )
        group_id = request.query_params.get("group_id")
        if group_id:
            tasks = tasks.filter(group_id=group_id)

        data = HomeworkTaskSerializer(tasks, many=True).data
        return success_response("Homework tasks fetched", {"tasks": data})

    def post(self, request):
        if request.user.role != "teacher":
            return error_response("Only teachers can create homework tasks", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        serializer = HomeworkTaskCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        group_id = serializer.validated_data["group_id"]
        group = Group.objects.filter(id=group_id, teacher=request.user).first()
        if not group:
            return error_response("Group not found or access denied", {"group_id": ["Invalid group"]}, status.HTTP_404_NOT_FOUND)

        task = HomeworkTask.objects.create(
            teacher=request.user,
            group=group,
            task_type="homework",
            title=serializer.validated_data["title"],
            description=serializer.validated_data.get("description", "") or "",
            speaking_topic="",
            speaking_level="",
            speaking_questions=[],
            due_at=serializer.validated_data.get("due_at"),
        )
        data = HomeworkTaskSerializer(task).data
        return success_response("Homework task created", {"task": data}, status.HTTP_201_CREATED)


class TeacherSpeakingTasksView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        if request.user.role != "teacher":
            return error_response("Only teachers can access speaking tasks", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        tasks = (
            HomeworkTask.objects.filter(teacher=request.user, is_active=True, task_type="speaking")
            .select_related("teacher", "group")
            .order_by("-created_at")
        )
        group_id = request.query_params.get("group_id")
        if group_id:
            tasks = tasks.filter(group_id=group_id)

        data = HomeworkTaskSerializer(tasks, many=True).data
        return success_response("Speaking tasks fetched", {"tasks": data})

    def post(self, request):
        if request.user.role != "teacher":
            return error_response("Only teachers can create speaking tasks", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        payload = request.data.copy()
        payload["task_type"] = "speaking"
        serializer = HomeworkTaskCreateSerializer(data=payload)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        group_id = serializer.validated_data["group_id"]
        group = Group.objects.filter(id=group_id, teacher=request.user).first()
        if not group:
            return error_response("Group not found or access denied", {"group_id": ["Invalid group"]}, status.HTTP_404_NOT_FOUND)

        task = HomeworkTask.objects.create(
            teacher=request.user,
            group=group,
            task_type="speaking",
            title=serializer.validated_data["title"],
            description=serializer.validated_data.get("description", "") or "",
            speaking_topic=serializer.validated_data.get("speaking_topic", "") or serializer.validated_data["title"],
            speaking_level=serializer.validated_data.get("speaking_level", "") or "",
            speaking_questions=serializer.validated_data.get("speaking_questions", []) or [],
            due_at=serializer.validated_data.get("due_at"),
        )
        data = HomeworkTaskSerializer(task).data
        return success_response("Speaking task created", {"task": data}, status.HTTP_201_CREATED)


class TeacherHomeworkTaskSubmissionsView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request, task_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can access submissions", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        task = HomeworkTask.objects.select_related("group", "teacher").filter(id=task_id, teacher=request.user).first()
        if not task:
            return error_response("Task not found or access denied", {"task": ["Task not found"]}, status.HTTP_404_NOT_FOUND)

        submissions = task.submissions.select_related("student", "student__group").order_by("-updated_at")
        data = {
            "task": HomeworkTaskSerializer(task).data,
            "submissions": HomeworkSubmissionSerializer(submissions, many=True).data,
        }
        return success_response("Homework submissions fetched", data)


class TeacherHomeworkSubmissionReviewView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def patch(self, request, submission_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can review submissions", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        submission = (
            HomeworkSubmission.objects.select_related("task", "task__teacher", "student")
            .filter(id=submission_id)
            .first()
        )
        if not submission or submission.task.teacher_id != request.user.id:
            return error_response("Submission not found or access denied", {"submission": ["Not found"]}, status.HTTP_404_NOT_FOUND)

        serializer = HomeworkSubmissionReviewSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        changed_fields = []
        if "status" in serializer.validated_data:
            submission.status = serializer.validated_data["status"]
            changed_fields.append("status")
        if "teacher_comment" in serializer.validated_data:
            submission.teacher_comment = serializer.validated_data["teacher_comment"] or ""
            changed_fields.append("teacher_comment")
        if "score" in serializer.validated_data:
            submission.score = serializer.validated_data["score"]
            changed_fields.append("score")

        if changed_fields:
            changed_fields.append("updated_at")
            submission.save(update_fields=changed_fields)

        data = HomeworkSubmissionSerializer(submission).data
        return success_response("Submission reviewed", {"submission": data})


class StudentHomeworkTasksView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        if request.user.role != "student":
            return error_response("Only students can access homework tasks", {"role": ["student only"]}, status.HTTP_403_FORBIDDEN)

        if not request.user.group_id:
            return success_response("Homework tasks fetched", {"tasks": []})

        tasks = (
            HomeworkTask.objects.filter(group_id=request.user.group_id, is_active=True, task_type="homework")
            .select_related("teacher", "group")
            .order_by("-created_at")
        )
        submissions_map = {
            submission.task_id: submission
            for submission in HomeworkSubmission.objects.filter(task__in=tasks, student=request.user).select_related("task")
        }

        payload = []
        for task in tasks:
            task_data = HomeworkTaskSerializer(task).data
            submission = submissions_map.get(task.id)
            task_data["my_submission"] = HomeworkSubmissionSerializer(submission).data if submission else None
            payload.append(task_data)

        return success_response("Homework tasks fetched", {"tasks": payload})


class StudentSpeakingTasksView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        if request.user.role != "student":
            return error_response("Only students can access speaking tasks", {"role": ["student only"]}, status.HTTP_403_FORBIDDEN)

        if not request.user.group_id:
            return success_response("Speaking tasks fetched", {"tasks": []})

        tasks = (
            HomeworkTask.objects.filter(group_id=request.user.group_id, is_active=True, task_type="speaking")
            .select_related("teacher", "group")
            .order_by("-created_at")
        )
        data = HomeworkTaskSerializer(tasks, many=True).data
        return success_response("Speaking tasks fetched", {"tasks": data})


class StudentHomeworkSubmitView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def post(self, request, task_id):
        if request.user.role != "student":
            return error_response("Only students can submit homework", {"role": ["student only"]}, status.HTTP_403_FORBIDDEN)

        if not request.user.group_id:
            return error_response("Student has no group", {"group": ["No group"]}, status.HTTP_400_BAD_REQUEST)

        task = HomeworkTask.objects.select_related("group", "teacher").filter(id=task_id, is_active=True).first()
        if not task:
            return error_response("Task not found", {"task": ["Task not found"]}, status.HTTP_404_NOT_FOUND)
        if task.group_id != request.user.group_id:
            return error_response("Task is not available for your group", {"task": ["Group mismatch"]}, status.HTTP_403_FORBIDDEN)

        serializer = HomeworkSubmissionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        submission, created = HomeworkSubmission.objects.get_or_create(
            task=task,
            student=request.user,
            defaults={
                "answer_text": serializer.validated_data["answer_text"],
                "status": "submitted",
            },
        )
        if not created:
            submission.answer_text = serializer.validated_data["answer_text"]
            submission.status = "submitted"
            submission.save(update_fields=["answer_text", "status", "updated_at"])

        data = HomeworkSubmissionSerializer(submission).data
        return success_response("Homework submitted", {"submission": data}, status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class VoiceTTSView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "voice_tts"

    def post(self, request):
        text = str(request.data.get("text", "")).strip()
        lang = str(request.data.get("lang", "")).strip()
        requested_voice = request.data.get("voice")
        audio_format = _normalize_voice_format(request.data.get("format"))
        max_text_chars = get_env_int("VOICE_TTS_MAX_TEXT_CHARS", DEFAULT_VOICE_TTS_MAX_TEXT_CHARS)

        if not text:
            return error_response("Validation error", {"text": ["Text is required"]}, status.HTTP_400_BAD_REQUEST)
        if len(text) > max_text_chars:
            return error_response(
                "Validation error",
                {"text": [f"Text is too long (max {max_text_chars} chars)"]},
                status.HTTP_400_BAD_REQUEST,
            )

        provider_order_raw = (
            os.environ.get("VOICE_TTS_PROVIDER_ORDER")
            or os.environ.get("VOICE_PROVIDER_ORDER")
            or os.environ.get("AI_PROVIDER")
            or "gemini,openai"
        )
        provider_order = [item.strip().lower() for item in provider_order_raw.split(",") if item.strip()]
        if not provider_order:
            provider_order = ["gemini", "openai"]

        last_error = "No voice provider configured"
        for provider in provider_order:
            voice_name = _normalize_voice_name(requested_voice, provider)
            try:
                if provider == "gemini":
                    audio_bytes, mime_type = _gemini_tts_request(text, lang, voice_name, audio_format)
                elif provider == "openai":
                    audio_bytes, mime_type = _openai_tts_request(text, voice_name, audio_format)
                else:
                    continue

                response = HttpResponse(audio_bytes, content_type=mime_type or "audio/mpeg")
                response["Cache-Control"] = "no-store"
                response["X-TTS-Provider"] = provider
                response["X-TTS-Voice"] = voice_name
                return response
            except Exception as exc:
                last_error = str(exc)
                logger.exception("[VOICE_TTS] provider failed: %s", provider)
                continue

        return error_response(
            "Voice TTS provider unavailable",
            {"tts": [last_error]},
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class AiChatMessagesView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        conversation, _ = AiConversation.objects.get_or_create(user=request.user)
        data = AiConversationSerializer(conversation, context={"request": request}).data
        return success_response("AI chat history fetched", data)

    def post(self, request):
        serializer = AiSendMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        conversation, _ = AiConversation.objects.get_or_create(user=request.user)
        text = serializer.validated_data.get("text", "")
        image_base64 = serializer.validated_data.get("imageBase64", "")
        level = serializer.validated_data.get("level", "")
        language = serializer.validated_data.get("language", "")
        group_title = serializer.validated_data.get("groupTitle", "")
        group_time = serializer.validated_data.get("groupTime", "")
        system_context = serializer.validated_data.get("systemContext", "")

        user_message = AiMessage.objects.create(
            conversation=conversation,
            role="user",
            text=text,
        )

        try:
            saved_image = save_ai_image_from_data_url(image_base64, request.user)
        except ValueError as exc:
            return error_response(
                "Validation error",
                {"imageBase64": [str(exc)]},
                status.HTTP_400_BAD_REQUEST,
            )

        if saved_image:
            filename, content = saved_image
            user_message.image.save(filename, content, save=True)

        if not level and request.user.group_id:
            try:
                group_value = request.user.group.title.lower()
                if "beginner" in group_value:
                    level = "beginner"
                elif "elementary" in group_value:
                    level = "elementary"
                elif "pre" in group_value and "inter" in group_value:
                    level = "pre-intermediate"
                elif "intermediate" in group_value:
                    level = "intermediate"
            except Exception:
                level = level or ""

        if not group_title and request.user.group_id:
            group_title = request.user.group.title
        if not group_time and request.user.group_id:
            group_time = request.user.group.time

        try:
            reply = generate_iman_ai_reply(
                text=text,
                image_data_url=image_base64,
                level=level,
                language=language,
                group_title=group_title,
                group_time=group_time,
                system_context=system_context,
                provider_order=_resolve_ai_chat_provider_order(),
                max_words=_resolve_ai_chat_max_words(),
                response_mode="chat",
            )
        except Exception:
            logger.exception("[IMAN_AI] unexpected provider failure")
            reply = build_ai_unavailable_reply(text, bool(image_base64))

        assistant_message = AiMessage.objects.create(
            conversation=conversation,
            role="assistant",
            text=reply,
        )

        if request.user.role == "student":
            try:
                update_student_progress_from_ai_chat(
                    request.user,
                    user_text=text,
                    has_image=bool(image_base64),
                    assistant_reply=reply,
                )
            except Exception:
                logger.exception("[IMAN_AI] progress update failed")

        conversation.save(update_fields=["updated_at"])

        messages = conversation.messages.all()
        payload = {
            "conversationId": conversation.id,
            "messages": AiMessageSerializer(messages, many=True, context={"request": request}).data,
            "lastAssistantMessage": AiMessageSerializer(assistant_message, context={"request": request}).data,
        }
        return success_response("AI reply generated", payload, status.HTTP_201_CREATED)


class AiChatMessagesStreamView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def post(self, request):
        serializer = AiSendMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        conversation, _ = AiConversation.objects.get_or_create(user=request.user)
        text = serializer.validated_data.get("text", "")
        image_base64 = serializer.validated_data.get("imageBase64", "")
        level = serializer.validated_data.get("level", "")
        language = serializer.validated_data.get("language", "")
        group_title = serializer.validated_data.get("groupTitle", "")
        group_time = serializer.validated_data.get("groupTime", "")
        system_context = serializer.validated_data.get("systemContext", "")

        user_message = AiMessage.objects.create(
            conversation=conversation,
            role="user",
            text=text,
        )

        try:
            saved_image = save_ai_image_from_data_url(image_base64, request.user)
        except ValueError as exc:
            return error_response(
                "Validation error",
                {"imageBase64": [str(exc)]},
                status.HTTP_400_BAD_REQUEST,
            )

        if saved_image:
            filename, content = saved_image
            user_message.image.save(filename, content, save=True)

        if not level and request.user.group_id:
            try:
                group_value = request.user.group.title.lower()
                if "beginner" in group_value:
                    level = "beginner"
                elif "elementary" in group_value:
                    level = "elementary"
                elif "pre" in group_value and "inter" in group_value:
                    level = "pre-intermediate"
                elif "intermediate" in group_value:
                    level = "intermediate"
            except Exception:
                level = level or ""

        if not group_title and request.user.group_id:
            group_title = request.user.group.title
        if not group_time and request.user.group_id:
            group_time = request.user.group.time

        def event_stream():
            yield _sse_event("start", {"conversationId": conversation.id, "userMessageId": user_message.id})

            try:
                reply = generate_iman_ai_reply(
                    text=text,
                    image_data_url=image_base64,
                    level=level,
                    language=language,
                    group_title=group_title,
                    group_time=group_time,
                    system_context=system_context,
                    provider_order=_resolve_ai_chat_provider_order(),
                    max_words=_resolve_ai_chat_max_words(),
                    response_mode="chat",
                )
            except Exception:
                logger.exception("[IMAN_AI_STREAM] unexpected provider failure")
                reply = build_ai_unavailable_reply(text, bool(image_base64))

            for chunk in _split_stream_chunks(reply):
                yield _sse_event("delta", {"text": chunk})

            assistant_message = AiMessage.objects.create(
                conversation=conversation,
                role="assistant",
                text=reply,
            )

            if request.user.role == "student":
                try:
                    update_student_progress_from_ai_chat(
                        request.user,
                        user_text=text,
                        has_image=bool(image_base64),
                        assistant_reply=reply,
                    )
                except Exception:
                    logger.exception("[IMAN_AI_STREAM] progress update failed")

            conversation.save(update_fields=["updated_at"])
            assistant_payload = AiMessageSerializer(assistant_message, context={"request": request}).data
            yield _sse_event("done", {"assistantMessage": assistant_payload, "conversationId": conversation.id})

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class AiSpeakingCheckView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def post(self, request):
        serializer = AiSpeakingCheckSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        question = serializer.validated_data["question"]
        transcript = serializer.validated_data["transcript"]
        level = serializer.validated_data.get("level", "")
        language = serializer.validated_data.get("language", "")
        group_title = serializer.validated_data.get("groupTitle", "")
        group_time = serializer.validated_data.get("groupTime", "")

        word_count = len([token for token in transcript.split() if token.strip()])
        if word_count < 4:
            return error_response(
                "Validation error",
                {"transcript": ["Please provide a longer answer (at least 4 words)."]},
                status.HTTP_400_BAD_REQUEST,
            )

        if not level and request.user.group_id:
            group_name = (request.user.group.title or "").lower()
            if "beginner" in group_name:
                level = "beginner"
            elif "elementary" in group_name:
                level = "elementary"
            elif "pre" in group_name and "inter" in group_name:
                level = "pre-intermediate"
            elif "intermediate" in group_name:
                level = "intermediate"

        if not group_title and request.user.group_id:
            group_title = request.user.group.title
        if not group_time and request.user.group_id:
            group_time = request.user.group.time

        try:
            analysis = generate_speaking_analysis(
                question=question,
                transcript=transcript,
                level=level,
                language=language,
                group_title=group_title,
                group_time=group_time,
            )
        except Exception:
            logger.exception("[IMAN_SPEAKING] analysis failed")
            return error_response(
                "Speaking AI is temporarily unavailable",
                {"speaking": ["AI service temporarily unavailable"]},
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if request.user.role == "student":
            try:
                update_student_progress_from_speaking(request.user, analysis, transcript)
            except Exception:
                logger.exception("[IMAN_SPEAKING] progress update failed")

        return success_response("Speaking analysis generated", analysis, status.HTTP_200_OK)


class FriendlyConversationsView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        conversations = (
            FriendlyConversation.objects.filter(participants=request.user)
            .prefetch_related("participants", "messages")
            .order_by("-updated_at")
        )
        data = [serialize_friendly_conversation_item(request, conversation) for conversation in conversations]
        return success_response("Friendly conversations fetched", {"conversations": data})

    def post(self, request):
        serializer = FriendlyConversationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        target_user_id = serializer.validated_data["targetUserId"]
        target = get_object_or_404(User.objects.select_related("group", "group__teacher"), id=target_user_id)
        if not can_user_chat_with_target(request.user, target):
            return error_response("Access denied", {"targetUserId": ["Cannot chat with this user"]}, status.HTTP_403_FORBIDDEN)

        conversation = get_or_create_direct_conversation(request.user, target)
        data = serialize_friendly_conversation_item(request, conversation)
        return success_response("Friendly conversation ready", {"conversation": data}, status.HTTP_201_CREATED)


class FriendlyConversationMessagesView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request, conversation_id):
        conversation = get_object_or_404(
            FriendlyConversation.objects.filter(participants=request.user).prefetch_related("messages", "messages__sender"),
            id=conversation_id,
        )
        messages = FriendlyMessageSerializer(conversation.messages.all(), many=True).data
        return success_response("Friendly messages fetched", {"conversationId": conversation.id, "messages": messages})

    def post(self, request, conversation_id):
        conversation = get_object_or_404(
            FriendlyConversation.objects.filter(participants=request.user).prefetch_related("participants"),
            id=conversation_id,
        )
        serializer = FriendlySendMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        message = FriendlyMessage.objects.create(
            conversation=conversation,
            sender=request.user,
            text=serializer.validated_data["text"],
        )
        conversation.save(update_fields=["updated_at"])
        payload = FriendlyMessageSerializer(message).data
        return success_response("Friendly message sent", {"message": payload}, status.HTTP_201_CREATED)


class GrammarTopicsView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        topics = GrammarTopic.objects.filter(is_active=True).select_related("created_by")
        data = GrammarTopicSerializer(topics, many=True).data
        return success_response("Grammar topics fetched", data)

    def post(self, request):
        if request.user.role != "teacher":
            return error_response("Only teachers can create grammar topics", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        serializer = GrammarTopicSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        topic = serializer.save(created_by=request.user)
        data = GrammarTopicSerializer(topic).data
        return success_response("Grammar topic created", data, status.HTTP_201_CREATED)


class SupportTicketListCreateView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get(self, request):
        if request.user.role == "teacher":
            tickets = SupportTicket.objects.filter(teacher=request.user).select_related("teacher", "student")
        else:
            tickets = SupportTicket.objects.filter(student=request.user).select_related("teacher", "student")

        data = SupportTicketSerializer(tickets, many=True).data
        return success_response("Support tickets fetched", data)

    def post(self, request):
        if request.user.role != "student":
            return error_response("Only students can create support requests", {"role": ["student only"]}, status.HTTP_403_FORBIDDEN)

        teacher = request.user.group.teacher if request.user.group and request.user.group.teacher_id else None
        if teacher is None:
            teacher = User.objects.filter(role="teacher", is_active=True).order_by("id").first()
        if teacher is None:
            return error_response("No support teacher", {"teacher": ["No active teacher found"]}, status.HTTP_400_BAD_REQUEST)
        message = (request.data.get("message") or "").strip()
        if len(message) < 3:
            return error_response("Validation error", {"message": ["Message is too short"]}, status.HTTP_400_BAD_REQUEST)

        ticket = SupportTicket.objects.create(
            student=request.user,
            teacher=teacher,
            message=message,
        )
        SupportTicketMessage.objects.create(
            ticket=ticket,
            sender_type="student",
            text=message,
            source="web",
            read_by_student_at=timezone.now(),
        )
        telegram_notified = notify_telegram_support_ticket(ticket)
        data = SupportTicketSerializer(ticket).data
        return success_response("Support request created", {"ticket": data, "telegramNotified": telegram_notified}, status.HTTP_201_CREATED)


class SupportTicketMessagesView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def get_ticket_for_user(self, request, ticket_id):
        if request.user.role == "teacher":
            return get_object_or_404(
                SupportTicket.objects.select_related("teacher", "student"),
                id=ticket_id,
                teacher=request.user,
            )
        return get_object_or_404(
            SupportTicket.objects.select_related("teacher", "student"),
            id=ticket_id,
            student=request.user,
        )

    def get(self, request, ticket_id):
        ticket = self.get_ticket_for_user(request, ticket_id)
        now = timezone.now()

        if request.user.role == "student":
            SupportTicketMessage.objects.filter(
                ticket=ticket,
                sender_type__in=["teacher", "support"],
                read_by_student_at__isnull=True,
            ).update(read_by_student_at=now)
        else:
            SupportTicketMessage.objects.filter(
                ticket=ticket,
                sender_type="student",
                read_by_support_at__isnull=True,
            ).update(read_by_support_at=now)

        messages = SupportTicketMessage.objects.filter(ticket=ticket).order_by("created_at")
        data = SupportTicketMessageSerializer(messages, many=True).data
        return success_response("Support messages fetched", {"ticketId": ticket.id, "messages": data})

    def post(self, request, ticket_id):
        ticket = self.get_ticket_for_user(request, ticket_id)
        text = str(request.data.get("text") or "").strip()
        if len(text) < 1:
            return error_response("Validation error", {"text": ["Message is required"]}, status.HTTP_400_BAD_REQUEST)
        if len(text) > 2000:
            return error_response("Validation error", {"text": ["Message is too long"]}, status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        if request.user.role == "student":
            sender_type = "student"
            read_by_student_at = now
            read_by_support_at = None
            ticket.message = text
            ticket.save(update_fields=["message", "updated_at"])
        else:
            sender_type = "teacher"
            read_by_student_at = None
            read_by_support_at = now
            ticket.teacher_reply = text
            ticket.teacher_reply_at = now
            ticket.status = "in_progress" if ticket.status == "open" else ticket.status
            ticket.save(update_fields=["teacher_reply", "teacher_reply_at", "status", "updated_at"])

            SupportTicketMessage.objects.filter(
                ticket=ticket,
                sender_type="student",
                read_by_support_at__isnull=True,
            ).update(read_by_support_at=now)

        message = SupportTicketMessage.objects.create(
            ticket=ticket,
            sender_type=sender_type,
            text=text,
            source="web",
            read_by_student_at=read_by_student_at,
            read_by_support_at=read_by_support_at,
        )

        telegram_notified = False
        if request.user.role == "student":
            telegram_notified = notify_telegram_support_message(ticket, text)

        payload = {
            "message": SupportTicketMessageSerializer(message).data,
            "telegramNotified": telegram_notified,
        }
        return success_response("Support message sent", payload, status.HTTP_201_CREATED)


class SupportTicketUpdateView(APIView):
    permission_classes = [IsAuthenticatedAndPaid]

    def patch(self, request, ticket_id):
        if request.user.role != "teacher":
            return error_response("Only teachers can update support requests", {"role": ["teacher only"]}, status.HTTP_403_FORBIDDEN)

        ticket = get_object_or_404(SupportTicket, id=ticket_id, teacher=request.user)
        serializer = SupportTicketUpdateSerializer(ticket, data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response("Validation error", serializer.errors, status.HTTP_400_BAD_REQUEST)

        serializer.save()
        data = SupportTicketSerializer(ticket).data
        return success_response("Support request updated", data)
