from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

from .subscription import has_active_subscription


class PaymentRequired(APIException):
    status_code = 402
    default_detail = {"code": "PAYMENT_REQUIRED", "message": "Payment required to access this feature."}
    default_code = "payment_required"


class IsAuthenticatedAndPaid(BasePermission):
    message = "Payment required to access this feature."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if getattr(user, "role", "") != "student":
            return True

        path = request.path.rstrip("/").lower()

        free_paths = (
            "/ratings/global",
            "/api/ratings/global",
            "/ratings/group",
            "/api/ratings/group",
            "/ratings/me",
            "/api/ratings/me",
            "/payments/create",
            "/api/payments/create",
            "/payments/status",
            "/api/payments/status",
            "/payments/manual-receipt",
            "/api/payments/manual-receipt",
            "/support/tickets",
            "/api/support/tickets",
        )

        if any(path == allowed or path.startswith(f"{allowed}/") for allowed in free_paths):
            return True

        if has_active_subscription(user):
            return True

        raise PaymentRequired()
