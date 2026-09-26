"""Transactional Excel-like J35 editing with canonical course assignments."""

from datetime import date, timedelta
import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .access import can_manage_j35_planner
from .course_services import allocate_course_sequence_number
from .j35_services import _unique_reference_code
from .j35_grid_models import J35GridCell, J35GridCourseLink
from .models import (
    ActivityLog, Camp, Course, CourseInstructor, CourseSession, Instructor,
    InstructorAllocation, Notification,
)
from .security import get_client_ip


def _date(value, label):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError(f"Choose a valid {label}.") from None


def _integer(value, label, *, required=False):
    if value in (None, "") and not required:
        return None
    try:
        parsed = int(value)
        if parsed < 1:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise ValidationError(f"Choose a valid {label}.") from None


def _session_progressed(session):
    """Protect all saved course work, including files and per-instructor inventory."""
    if session.status in (
        CourseSession.Status.IN_PROGRESS, CourseSession.Status.COMPLETED,
        CourseSession.Status.CANCELLED,
    ):
        return True
    if (
        session.registration_published or session.course_information_sent_at
        or session.external_upload_generated_at or session.external_upload_confirmed_at
    ):
        return True
    return any(
        getattr(session, relation).exists()
        for relation in (
            "registrations", "training_records", "roster_snapshots",
            "stamped_list_archives", "inventory_usage", "instructor_inventory_usage",
            "instructor_inventory_movements", "source_evidence",
        )
    )


def _snapshot(allocation):
    return {
        "instructor": allocation.instructor_id,
        "camp": allocation.camp_id,
        "activity": allocation.activity,
        "start_date": allocation.start_date.isoformat(),
        "end_date": allocation.end_date.isoformat(),
        "status": allocation.status,
        "kind": allocation.allocation_kind,
        "session": str(allocation.session.public_id) if allocation.session_id else None,
    }


def _detach_assignment(allocation, cell):
    """Unassign only work owned by this grid; retain empty course history as draft."""
    if not allocation.session_id:
        return
    session = CourseSession.objects.select_for_update().get(pk=allocation.session_id)
    if _session_progressed(session):
        raise ValidationError(
            "This course already has saved work or published registration. "
            "Its assignment and dates must be managed from the course. You can still change the cell colour."
        )
    if not cell.owns_assignment:
        raise ValidationError(
            "This assignment was created outside this grid. Manage it from the course to preserve its history."
        )
    other = InstructorAllocation.objects.filter(
        instructor_id=allocation.instructor_id, session=session,
        status=InstructorAllocation.Status.PUBLISHED,
    ).exclude(pk=allocation.pk).exists()
    if not other:
        CourseInstructor.objects.filter(
            session=session, instructor_id=allocation.instructor_id
        ).delete()
    if (
        J35GridCourseLink.objects.filter(session=session).exists()
        and not session.instructor_assignments.exists()
    ):
        session.status = CourseSession.Status.DRAFT
        session.confirmed_at = None
        session.registration_published = False
        session.save(update_fields=["status", "confirmed_at", "registration_published", "updated_at"])
    cell.owns_assignment = False


def _matching_session(course, camp, start, end, selected=None):
    # The caller holds the course-row lock, serialising confirmations by course.
    # Two instructors confirming the same camp/date range join the same session.
    candidates = CourseSession.objects.select_for_update().filter(
        course=course, camp=camp, start_date=start,
    ).filter(Q(end_date=end) | Q(end_date__isnull=True, start_date=end)).exclude(
        status=CourseSession.Status.CANCELLED
    )
    if selected:
        session = candidates.filter(pk=selected).first()
        if session is None:
            raise ValidationError("The selected existing course does not match this camp, course and date range.")
        return session
    found = list(candidates.order_by("pk")[:2])
    if len(found) > 1:
        raise ValidationError(
            "More than one existing course matches these dates. Choose the intended existing course in cell details."
        )
    return found[0] if found else None


def _conflicts(allocation, target_session_id=None):
    others = InstructorAllocation.objects.filter(
        instructor_id=allocation.instructor_id,
        status__in=[InstructorAllocation.Status.DRAFT, InstructorAllocation.Status.PUBLISHED],
        start_date__lte=allocation.end_date, end_date__gte=allocation.start_date,
    )
    if allocation.pk:
        others = others.exclude(pk=allocation.pk)
    found = [f"{item.activity} ({item.start_date:%d %b}–{item.end_date:%d %b})" for item in others[:4]]
    sessions = CourseSession.objects.filter(
        instructor_assignments__instructor_id=allocation.instructor_id,
        start_date__lte=allocation.end_date,
    ).filter(
        Q(end_date__gte=allocation.start_date)
        | Q(end_date__isnull=True, start_date__gte=allocation.start_date)
    ).exclude(status__in=[CourseSession.Status.CANCELLED, CourseSession.Status.DRAFT])
    if target_session_id:
        sessions = sessions.exclude(pk=target_session_id)
    if allocation.session_id:
        sessions = sessions.exclude(pk=allocation.session_id)
    represented = others.values_list("session_id", flat=True)
    sessions = sessions.exclude(pk__in=[pk for pk in represented if pk])
    found.extend(f"{s.course.title_english} ({s.start_date:%d %b})" for s in sessions.select_related("course")[:4])
    return found


def _notify(allocation, user, message):
    instructor = allocation.instructor
    if instructor.user_id and instructor.user.is_active:
        link = (
            reverse("instructor_course_workspace", kwargs={"public_id": allocation.session.public_id})
            if allocation.session_id else reverse("notification_inbox")
        )
        Notification.objects.create(
            recipient=instructor.user,
            category=Notification.Category.INFORMATION,
            title="J35 assignment confirmed" if allocation.status == InstructorAllocation.Status.PUBLISHED else "J35 assignment changed",
            message=message,
            link=link,
        )
        allocation.notified_at = timezone.now()
        allocation.save(update_fields=["notified_at", "updated_at"])


@transaction.atomic
def clear_grid_cell(*, user, data, request=None):
    """Clear an unconfirmed draft from the sheet while keeping its audit history."""
    if not can_manage_j35_planner(user):
        raise PermissionDenied("You do not have permission to edit J35.")
    instructor_id = _integer(data.get("instructor"), "instructor", required=True)
    Instructor.objects.select_for_update().filter(pk=instructor_id).first()
    allocation_id = _integer(data.get("allocation"), "allocation", required=True)
    allocation = InstructorAllocation.objects.select_for_update().filter(
        pk=allocation_id, instructor_id=instructor_id,
    ).first()
    if allocation is None:
        raise ValidationError("This cell is unavailable. Refresh J35.")
    if allocation.status != InstructorAllocation.Status.DRAFT or allocation.session_id:
        raise ValidationError("Return the assignment to Draft before clearing it. Linked courses must be managed from the course.")
    cell = J35GridCell.objects.select_for_update().filter(allocation=allocation).first()
    try:
        revision = int(data.get("revision", -1))
    except (TypeError, ValueError):
        revision = -1
    if revision != (cell.revision if cell else 0):
        raise ValidationError("This cell changed in another window. Refresh J35 before clearing it.")
    before = _snapshot(allocation)
    allocation.status = InstructorAllocation.Status.CANCELLED
    allocation.updated_by = user
    allocation.save(update_fields=["status", "updated_by", "updated_at"])
    if cell:
        cell.revision += 1
        cell.save(update_fields=["revision"])
    ActivityLog.objects.create(
        actor=user, action=ActivityLog.Action.CANCEL,
        object_type="J35 grid allocation", object_id=str(allocation.public_id),
        description="Draft J35 cell cleared; allocation history retained.",
        details={"before": before, "after": _snapshot(allocation)},
        ip_address=get_client_ip(request) if request else None,
    )
    return allocation


@transaction.atomic
def save_grid_cell(*, user, data, request=None):
    """Validate one instructor/date-range edit, returning the persisted allocation.

    No canonical course or assignment is created for a draft. Client supplied
    ownership, notified timestamps, course status and confirmation dates are
    deliberately ignored. Revisions reject stale browser writes.
    """
    if not can_manage_j35_planner(user):
        raise PermissionDenied("You do not have permission to edit J35.")
    instructor_id = _integer(data.get("instructor"), "instructor", required=True)
    instructor = Instructor.objects.select_for_update(of=("self",)).select_related("user").filter(
        pk=instructor_id, active=True
    ).first()
    if instructor is None:
        raise ValidationError("Choose an active instructor.")

    allocation_id = _integer(data.get("allocation"), "allocation")
    allocation = None
    if allocation_id:
        allocation = InstructorAllocation.objects.select_for_update(of=("self",)).select_related(
            "session", "session__course", "camp", "instructor__user"
        ).filter(pk=allocation_id, instructor=instructor).first()
        if allocation is None or allocation.status == InstructorAllocation.Status.CANCELLED:
            raise ValidationError("This allocation is unavailable. Refresh the J35 grid.")
        cell = J35GridCell.objects.select_for_update().filter(allocation=allocation).first()
        current_revision = cell.revision if cell else 0
        try:
            supplied_revision = int(data.get("revision", -1))
        except (TypeError, ValueError):
            supplied_revision = -1
        if supplied_revision != current_revision:
            raise ValidationError("This cell changed in another window. Refresh J35 before saving again.")
        if not cell:
            cell = J35GridCell(allocation=allocation, course=allocation.session.course if allocation.session_id else None)
    else:
        allocation = InstructorAllocation(instructor=instructor, created_by=user)
        cell = J35GridCell(allocation=allocation)

    before = _snapshot(allocation) if allocation.pk else None
    previous_course_id = cell.course_id or (allocation.session.course_id if allocation.session_id else None)
    previous_color = cell.color
    previous_notes = allocation.notes
    previous_override = allocation.override_reason
    start = _date(data.get("start_date"), "start date")
    end = _date(data.get("end_date") or start, "end date")
    if end < start or (end - start).days > 365:
        raise ValidationError("The end date must be on or after the start date, within one year.")
    activity = str(data.get("activity") or "").strip()
    if not activity or len(activity) > 200:
        raise ValidationError("Enter a camp or task, up to 200 characters.")
    kind = data.get("kind", "course")
    if kind not in InstructorAllocation.AllocationKind.values:
        raise ValidationError("Choose a course, administrative duty, leave or other task.")
    status = data.get("status", "draft")
    if status not in ("draft", "confirmed"):
        raise ValidationError("Choose Draft or Confirmed.")
    color = str(data.get("color") or "#ffffff").lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        raise ValidationError("Choose a valid cell colour.")
    notes = str(data.get("notes") or "").strip()
    override = str(data.get("override_reason") or "").strip()
    if len(notes) > 4000 or len(override) > 2000:
        raise ValidationError("Shorten the notes or conflict override reason.")

    course_id = _integer(data.get("course"), "course") if kind == "course" else None
    course = Course.objects.select_for_update().filter(pk=course_id).first() if course_id else None
    if course_id and (course is None or (not course.active and course_id != previous_course_id)):
        raise ValidationError("Choose an active course.")
    camp_id = _integer(data.get("camp"), "camp")
    camp = Camp.objects.filter(pk=camp_id).first() if camp_id else None
    if camp_id and (camp is None or (not camp.active and camp_id != allocation.camp_id)):
        raise ValidationError("Choose an active camp.")
    if kind == "course" and camp is None:
        camp = Camp.objects.filter(name__iexact=activity, active=True).first()
    if status == "confirmed" and kind == "course" and (course is None or camp is None):
        raise ValidationError("Choose the course and an existing camp in cell details before confirming.")

    target = None
    selected = _integer(data.get("session"), "existing course")
    if status == "confirmed" and kind == "course":
        target = _matching_session(course, camp, start, end, selected)
    next_status = (
        InstructorAllocation.Status.PUBLISHED if status == "confirmed"
        else InstructorAllocation.Status.DRAFT
    )
    changed_assignment = bool(before) and (
        before["start_date"] != start.isoformat() or before["end_date"] != end.isoformat()
        or before["camp"] != (camp.pk if camp else None)
        or before["kind"] != kind or previous_course_id != course_id
        or before["status"] != next_status
        or (target is not None and allocation.session_id != target.pk)
    )
    if changed_assignment and allocation.session_id:
        _detach_assignment(allocation, cell)
        allocation.session = None
    # Metadata edits of legacy confirmed courses do not rewrite their session.
    if allocation.training_need_id and (
        camp is None or allocation.training_need.camp_id != camp.pk
    ):
        raise ValidationError("This allocation is linked to a camp request. Manage its camp from that request.")

    allocation.activity = activity
    allocation.camp = camp
    allocation.start_date = start
    allocation.end_date = end
    allocation.status = next_status
    allocation.allocation_kind = kind
    allocation.notes = notes
    allocation.override_reason = override
    allocation.updated_by = user
    conflicts = _conflicts(allocation, target.pk if target else None)
    if conflicts and not override:
        raise ValidationError("This instructor has overlapping work: " + "; ".join(conflicts) + ". Add a conflict override reason in cell details if intentional.")

    if status == "confirmed" and kind == "course":
        if target is None:
            contact = camp.contacts.filter(active=True).order_by("-is_primary", "name").first()
            target = CourseSession.objects.create(
                course=course, camp=camp, start_date=start, end_date=end,
                status=CourseSession.Status.REGISTRATION_CLOSED,
                confirmed_at=timezone.now(), registration_published=False,
                reference_code=_unique_reference_code(course, camp, start),
                sequence_number=allocate_course_sequence_number(), created_by=user,
                poc_name=contact.name if contact else "",
                poc_contact_number=contact.phone if contact else "",
            )
            J35GridCourseLink.objects.create(session=target, created_by=user)
        elif target.status == CourseSession.Status.DRAFT:
            target.status = CourseSession.Status.REGISTRATION_CLOSED
            target.confirmed_at = target.confirmed_at or timezone.now()
            target.save(update_fields=["status", "confirmed_at", "updated_at"])
        if _session_progressed(target) and not CourseInstructor.objects.filter(
            session=target, instructor=instructor
        ).exists():
            raise ValidationError(
                "This course already has saved work or published registration. "
                "Add or change its instructors from the course workspace."
            )
        assignment, created_assignment = CourseInstructor.objects.get_or_create(
            session=target, instructor=instructor
        )
        cell.owns_assignment = cell.owns_assignment or created_assignment
        allocation.session = target
    elif not allocation.session_id or changed_assignment:
        allocation.session = None

    allocation.full_clean()
    allocation.save()
    cell.allocation = allocation
    cell.course = course
    cell.color = color
    cell.revision = (cell.revision + 1) if cell.pk else 1
    cell.full_clean()
    cell.save()

    after = _snapshot(allocation)
    if before != after or previous_course_id != course_id or previous_color != color or previous_notes != notes or previous_override != override:
        ActivityLog.objects.create(
            actor=user, action=ActivityLog.Action.UPDATE if before else ActivityLog.Action.CREATE,
            object_type="J35 grid allocation", object_id=str(allocation.public_id),
            description="J35 grid assignment saved.",
            details={"before": before, "after": after, "colour": color, "course": course_id, "override_reason": override},
            ip_address=get_client_ip(request) if request else None,
        )
    first_confirmation = next_status == InstructorAllocation.Status.PUBLISHED and (
        not before or before["status"] != InstructorAllocation.Status.PUBLISHED or changed_assignment
        or (kind != "course" and before["activity"] != activity)
    )
    if first_confirmation:
        _notify(
            allocation, user,
            f"You are assigned to {activity} from {start:%d %b %Y} to {end:%d %b %Y}."
            + (f" Course: {course.title_english}." if course else ""),
        )
    elif before and before["status"] == InstructorAllocation.Status.PUBLISHED and next_status == InstructorAllocation.Status.DRAFT:
        _notify(allocation, user, f"Your {before['activity']} assignment for {before['start_date']} was returned to draft by the planner.")
    return allocation
