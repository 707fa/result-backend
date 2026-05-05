from django.db import models

from django.conf import settings


class Group(models.Model):
    LEVEL_CHOICES = (
        ("Beginner", "Beginner"),
        ("Elementary", "Elementary"),
        ("Pre-Intermediate", "Pre-Intermediate"),
        ("Intermediate", "Intermediate"),
        ("Upper-Intermediate", "Upper-Intermediate"),
        ("Advanced", "Advanced"),
    )

    DAYS_PATTERN_CHOICES = (
        ("mwf", "MWF"),
        ("tts", "TTS"),
    )

    title = models.CharField(max_length=255, choices=LEVEL_CHOICES)
    time = models.CharField(max_length=50)
    days_pattern = models.CharField(max_length=10, choices=DAYS_PATTERN_CHOICES)


    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teaching_groups",
        limit_choices_to={"role": "teacher"},
    )

    def __str__(self):
        return f"{self.title} - {self.time} - {self.days_pattern}"
