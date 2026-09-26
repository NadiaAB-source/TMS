"""Signals that protect data invariants outside bespoke form views."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from .models import Instructor
from .normalization import normalize_email


@receiver(pre_save, sender=get_user_model())
def normalize_user_email(sender, instance, **kwargs):
    instance.email = normalize_email(instance.email)


def _sync_after_commit(instructor_id):
    def sync():
        from .staff_groups import synchronise_staff_groups

        instructor = Instructor.objects.filter(pk=instructor_id).first()
        if instructor and instructor.user_id:
            synchronise_staff_groups(instructor)

    transaction.on_commit(sync)


@receiver(post_save, sender=Instructor)
def synchronise_instructor_after_save(sender, instance, **kwargs):
    if instance.user_id:
        _sync_after_commit(instance.pk)


@receiver(m2m_changed, sender=Instructor.roles.through)
def synchronise_instructor_after_roles_change(sender, instance, action, **kwargs):
    if action in {"post_add", "post_remove", "post_clear"} and instance.user_id:
        _sync_after_commit(instance.pk)
