import re
from datetime import timedelta
import os

from django.utils import timezone


def _normalize_phone(value):
    phone = str(value or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits.startswith("998") and len(digits) >= 12:
        digits = digits[:12]
    elif len(digits) == 9:
        digits = f"998{digits}"
    elif len(digits) == 10 and digits.startswith("0"):
        digits = f"998{digits[1:]}"

    if len(digits) == 12 and digits.startswith("998"):
        return f"+{digits}"
    return phone


def _parse_free_access_phones():
    raw = os.environ.get("FREE_ACCESS_PHONES", "")
    if not raw:
        return set()
    chunks = re.split(r"[,\n;]+", str(raw))
    phones = set()
    for chunk in chunks:
        normalized = _normalize_phone(chunk)
        if normalized:
            phones.add(normalized)
    return phones


def has_free_access_override(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "role", "") != "student":
        return True

    phone = _normalize_phone(getattr(user, "phone", ""))
    return phone in _parse_free_access_phones()


def has_active_subscription(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if has_free_access_override(user):
        return True

    if getattr(user, "role", "") != "student":
        return True

    paid_until = getattr(user, "paid_until", None)
    is_paid = bool(getattr(user, "is_paid", False))

    if paid_until is None:
        return is_paid

    if paid_until >= timezone.now():
        return True

    if is_paid:
        user.is_paid = False
        user.save(update_fields=["is_paid"])
    return False


def grant_subscription(user, days: int = 30):
    now = timezone.now()
    current_paid_until = getattr(user, "paid_until", None)
    start_from = current_paid_until if current_paid_until and current_paid_until > now else now
    next_paid_until = start_from + timedelta(days=max(days, 1))

    user.is_paid = True
    user.paid_until = next_paid_until
    user.save(update_fields=["is_paid", "paid_until"])
    return next_paid_until


def get_subscription_payload(user):
    paid_until = getattr(user, "paid_until", None)
    is_overridden = has_free_access_override(user)
    is_paid = has_active_subscription(user)
    return {
        "isPaid": is_paid,
        "paidUntil": paid_until.isoformat() if paid_until else None,
        "required": getattr(user, "role", "") == "student" and not is_overridden,
    }
