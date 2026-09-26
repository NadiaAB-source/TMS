"""Date-ordered visible numbering for the clean-start site; UUID links stay stable."""
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_delete
from django.dispatch import receiver


def enabled():
    return bool(getattr(settings, 'IQARUS_CHRONOLOGICAL_NUMBERING', False))


def _renumber(counter):
    from .models import CourseSession
    sessions = list(CourseSession.objects.order_by(F('start_date').asc(nulls_last=True), 'created_at', 'pk').only('pk', 'sequence_number'))
    if any(s.sequence_number != i for i, s in enumerate(sessions, 1)):
        # Vacate all unique numbers before assigning the new chronological order.
        CourseSession.objects.all().update(sequence_number=None)
        for number, session in enumerate(sessions, 1):
            session.sequence_number = number
        CourseSession.objects.bulk_update(sessions, ['sequence_number'], batch_size=200)
    counter.next_number = len(sessions) + 1
    counter.save(update_fields=['next_number'])
    return {s.pk: s.sequence_number for s in sessions}


def renumber_courses():
    if not enabled():
        return {}
    from .models import CourseSequenceCounter
    with transaction.atomic():
        counter = CourseSequenceCounter.objects.select_for_update().filter(key='course_session', enabled=True).first()
        return _renumber(counter) if counter else {}


def save_session(instance, parent_save, args, kwargs):
    if not enabled():
        return parent_save(*args, **kwargs)
    from .models import CourseSequenceCounter, CourseSession
    with transaction.atomic():
        counter = CourseSequenceCounter.objects.select_for_update().filter(key='course_session', enabled=True).first()
        if not counter:
            return parent_save(*args, **kwargs)
        # Another course/date edit may have changed this instance's cached number.
        instance.sequence_number = CourseSession.objects.filter(pk=instance.pk).values_list('sequence_number', flat=True).first() if instance.pk else None
        result = parent_save(*args, **kwargs)
        instance.sequence_number = _renumber(counter).get(instance.pk)
        return result


@receiver(post_delete, sender='portal.CourseSession', dispatch_uid='clean_start_numbering_delete')
def course_removed(sender, instance, **kwargs):
    renumber_courses()
