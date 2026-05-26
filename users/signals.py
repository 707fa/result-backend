from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import User, PaymentTransaction
from groups.models import Group
from .stateversion import StateVersion


@receiver(post_save, sender=User)
def user_changed(sender, instance, **kwargs):
    StateVersion.bump()


@receiver(post_delete, sender=User)
def user_deleted(sender, instance, **kwargs):
    StateVersion.bump()


@receiver(post_save, sender=Group)
def group_changed(sender, instance, **kwargs):
    StateVersion.bump()


@receiver(post_delete, sender=Group)
def group_deleted(sender, instance, **kwargs):
    StateVersion.bump()


@receiver(post_save, sender=PaymentTransaction)
def payment_changed(sender, instance, **kwargs):
    StateVersion.bump()


@receiver(post_delete, sender=PaymentTransaction)
def payment_deleted(sender, instance, **kwargs):
    StateVersion.bump()
