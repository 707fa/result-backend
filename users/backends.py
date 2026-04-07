from django.contrib.auth import get_user_model

User = get_user_model()


def _phone_candidates(raw_phone):
    phone = str(raw_phone or "").strip()
    if not phone:
        return []

    digits = "".join(ch for ch in phone if ch.isdigit())
    candidates = []

    def add(value):
        value = str(value or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    add(phone)

    if phone.startswith("+"):
        add(phone[1:])

    if digits:
        add(digits)

        if len(digits) == 9:
            add(f"+998{digits}")

        if digits.startswith("998") and len(digits) >= 12:
            local = digits[3:12]
            add(local)
            add(f"+998{local}")

    return candidates


class PhoneBackend:
    def authenticate(self, request, phone=None, password=None, **kwargs):
        if phone is None or password is None:
            return None

        candidates = _phone_candidates(phone)
        if not candidates:
            return None

        users = list(User.objects.filter(phone__in=candidates).order_by("id"))

        exact = next((user for user in users if str(user.phone).strip() == str(phone).strip()), None)
        if exact and exact.check_password(password):
            return exact

        for user in users:
            if user.check_password(password):
                return user

        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
