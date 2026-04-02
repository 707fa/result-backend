from django.db import models


class ScoreLog(models.Model):
    teacher = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="given_scores",
    )
    student = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="received_scores",
    )
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="score_logs",
    )
    delta = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.teacher_id} -> {self.student_id}: {self.delta}"


class RatingRecalcLog(models.Model):
    STATUS_CHOICES = (
        ("success", "Success"),
        ("failed", "Failed"),
    )

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="recalc_logs",
    )
    days_pattern = models.CharField(max_length=10)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    message = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.group_id} - {self.status}"