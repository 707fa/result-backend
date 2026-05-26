from django.conf import settings


class SecurityHeadersMiddleware:
    """Add conservative browser security headers for API/admin responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        is_admin = request.path.startswith("/admin")

        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("X-Frame-Options", "SAMEORIGIN" if is_admin else "DENY")
        response.setdefault("Referrer-Policy", getattr(settings, "SECURE_REFERRER_POLICY", "same-origin"))
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )

        if is_admin:
            response.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; "
                "base-uri 'self'; "
                "object-src 'none'; "
                "form-action 'self'; "
                "frame-ancestors 'self'; "
                "frame-src 'self'; "
                "img-src 'self' data: https:; "
                "media-src 'self' data: blob:; "
                "font-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "connect-src 'self'",
            )
        else:
            response.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; "
                "base-uri 'self'; "
                "object-src 'none'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "frame-src 'none'; "
                "img-src 'self' data: https:; "
                "media-src 'self' data: blob:; "
                "font-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; "
                "connect-src 'self' https://api.telegram.org"
                + ("; upgrade-insecure-requests" if not getattr(settings, "DEBUG", False) else ""),
            )

        if request.path.startswith("/api/"):
            response.setdefault("Cache-Control", "no-store, max-age=0")
            response.setdefault("Pragma", "no-cache")

        return response
