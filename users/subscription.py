from datetime import timedelta

from django.utils import timezone


def has_active_subscription(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False

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
    return {
        "isPaid": has_active_subscription(user),
        "paidUntil": paid_until.isoformat() if paid_until else None,
        "required": getattr(user, "role", "") == "student",
    }
