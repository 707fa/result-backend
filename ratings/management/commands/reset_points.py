from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from users.models import User
from groups.models import Group
from ratings.models import RatingRecalcLog


class Command(BaseCommand):
    help = "Reset student points"

    def handle(self, *args, **kwargs):
        now = timezone.now()

        patterns = ["mwf", "tts"]

        for pattern in patterns:
            groups = Group.objects.filter(days_pattern=pattern)

            for group in groups:
                log = RatingRecalcLog.objects.create(
                    group=group,
                    days_pattern=pattern,
                    started_at=now,
                    status="success",
                )

                try:
                    with transaction.atomic():
                        students = User.objects.filter(
                            role="student",
                            group=group
                        )

                        count = students.count()

                        students.update(points=0)

                        log.message = f"Reset {count} students"
                        log.finished_at = timezone.now()
                        log.status = "success"
                        log.save()

                        self.stdout.write(
                            self.style.SUCCESS(
                                f"[{pattern}] Group {group.id} reset {count}"
                            )
                        )

                except Exception as e:
                    log.status = "failed"
                    log.message = str(e)
                    log.finished_at = timezone.now()
                    log.save()

                    self.stdout.write(
                        self.style.ERROR(
                            f"[{pattern}] Group {group.id} error: {e}"
                        )
                    )