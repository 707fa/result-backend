from django.db import models


class StateVersion(models.Model):
    version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "State Version"
        verbose_name_plural = "State Versions"

    @classmethod
    def bump(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        obj.version += 1
        obj.save(update_fields=["version"])
        return obj.version

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"version": 0})
        return obj.version
