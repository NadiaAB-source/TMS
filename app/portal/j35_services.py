"""Small, auditable services for the J35 weekly planning workflow.

The planner deliberately keeps its recommendation logic explainable.  It does
not make a staffing decision itself: it ranks available people and tells the
planner why someone is unavailable or lower in the list.  The planner can
override a conflict only by recording a reason, which is retained on the
allocation and in the Activity Log.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .course_services import (
    allocate_course_sequence_number,
    safe_reference_code,
)
from .models import (
    ActivityLog,
    Camp,
    CampContact,
    CourseInstructor,
    CourseSession,
    Instructor,
    InstructorAllocation,
    J35TrainingNeed,
    Notification,
)
from .security import get_client_ip


OPEN_NEED_STATUSES = (
    J35TrainingNeed.Status.NEW,
    J35TrainingNeed.Status.CONTACTED,
    J35TrainingNeed.Status.REQUESTED,
    J35TrainingNeed.Status.PROPOSED,
)


def planner_week(value: date | None = None) -> date:
    """Return the Monday that anchors a planner week."""

    value = value or timezone.localdate()
    return value - timedelta(days=value.weekday())


def date_range_overlaps(
    start_date: date | None,
    end_date: date | None,
    range_start: date | None,
    range_end: date | None,
) -> bool:
    if not start_date or not range_start:
        return False
    return start_date <= (range_end or range_start) and (end_date or start_date) >= range_start


def allocation_conflicts(
    instructor,
    start_date,
    end_date,
    *,
    exclude_need=None,
    exclude_session_id=None,
):
    """Return readable conflicts from both planner and canonical course data."""

    if not start_date:
        return []
    end_date = end_date or start_date
    allocation_filter = Q(
        start_date__lte=end_date,
        end_date__gte=start_date,
        status__in=(
            InstructorAllocation.Status.DRAFT,
            InstructorAllocation.Status.PUBLISHED,
        ),
    )
    allocations = InstructorAllocation.objects.filter(
        allocation_filter,
        instructor=instructor,
    ).select_related("session", "session__course", "camp")
    if exclude_need is not None:
        allocations = allocations.exclude(training_need=exclude_need)

    seen = set()
    conflicts = []
    for allocation in allocations:
        label = allocation.activity or "J35 allocation"
        if allocation.session_id:
            label = allocation.session.course.title_english or label
        marker = ("allocation", allocation.pk)
        seen.add(marker)
        conflicts.append(f"{label} ({allocation.start_date:%d %b}–{allocation.end_date:%d %b})")

    sessions = (
        CourseSession.objects.exclude(status=CourseSession.Status.CANCELLED)
        .filter(
            instructor_assignments__instructor=instructor,
            start_date__isnull=False,
            start_date__lte=end_date,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=start_date))
        .select_related("course", "camp")
        .distinct()
    )
    if exclude_need is not None and exclude_need.proposed_session_id:
        sessions = sessions.exclude(pk=exclude_need.proposed_session_id)
    if exclude_session_id:
        sessions = sessions.exclude(pk=exclude_session_id)
    for session in sessions:
        if any(allocation.session_id == session.id for allocation in allocations):
            continue
        label = session.course.title_english or "Course"
        session_end = session.end_date or session.start_date
        conflicts.append(f"{label} ({session.start_date:%d %b}–{session_end:%d %b})")
    return conflicts


def _workload_counts(instructors, week_start, week_end):
    """Return current-week and trailing-four-week planner workload counts.

    Counts use approved and draft allocations because both reserve a person's
    time.  Canonical course assignments missing a planner allocation are added
    so recommendations remain safe during the transition from older data.
    """

    instructor_ids = [instructor.id for instructor in instructors]
    if not instructor_ids:
        return {}, {}
    active_statuses = (
        InstructorAllocation.Status.DRAFT,
        InstructorAllocation.Status.PUBLISHED,
    )
    allocation_rows = InstructorAllocation.objects.filter(
        instructor_id__in=instructor_ids,
        status__in=active_statuses,
        start_date__lte=week_end,
        end_date__gte=week_start,
    ).values_list("instructor_id", "session_id")
    weekly = Counter(instructor_id for instructor_id, _ in allocation_rows)

    trailing_start = week_start - timedelta(days=28)
    trailing_rows = InstructorAllocation.objects.filter(
        instructor_id__in=instructor_ids,
        status__in=active_statuses,
        start_date__lte=week_end,
        end_date__gte=trailing_start,
    ).values_list("instructor_id", "session_id")
    recent = Counter(instructor_id for instructor_id, _ in trailing_rows)

    # A historic course assignment might have no J35 row.  Count it once when
    # it overlaps the same period, without inflating a course already counted
    # through an InstructorAllocation.
    planned_session_pairs = {
        (instructor_id, session_id)
        for instructor_id, session_id in allocation_rows
        if session_id
    }
    recent_planned_session_pairs = {
        (instructor_id, session_id)
        for instructor_id, session_id in trailing_rows
        if session_id
    }
    course_assignments = CourseInstructor.objects.filter(
        instructor_id__in=instructor_ids,
        session__status__in=(
            CourseSession.Status.DRAFT,
            CourseSession.Status.REGISTRATION_OPEN,
            CourseSession.Status.REGISTRATION_CLOSED,
            CourseSession.Status.IN_PROGRESS,
        ),
        session__start_date__isnull=False,
        session__start_date__lte=week_end,
    ).filter(Q(session__end_date__isnull=True) | Q(session__end_date__gte=week_start))
    for assignment in course_assignments.values_list("instructor_id", "session_id"):
        if assignment not in planned_session_pairs:
            weekly[assignment[0]] += 1

    recent_course_assignments = CourseInstructor.objects.filter(
        instructor_id__in=instructor_ids,
        session__status__in=(
            CourseSession.Status.DRAFT,
            CourseSession.Status.REGISTRATION_OPEN,
            CourseSession.Status.REGISTRATION_CLOSED,
            CourseSession.Status.IN_PROGRESS,
        ),
        session__start_date__isnull=False,
        session__start_date__lte=week_end,
    ).filter(
        Q(session__end_date__isnull=True) | Q(session__end_date__gte=trailing_start)
    )
    for assignment in recent_course_assignments.values_list(
        "instructor_id", "session_id"
    ):
        if assignment not in recent_planned_session_pairs:
            recent[assignment[0]] += 1
    return dict(weekly), dict(recent)


def _area_experience(instructors, camp):
    """Return staff who have previously served the camp's area.

    Area is a fairness/relevance preference, not a hard exclusion.  The lead
    remains free to assign a person from another area where that is better for
    the client or team.
    """

    if not camp or not camp.area:
        return set()
    instructor_ids = [instructor.id for instructor in instructors]
    return set(
        InstructorAllocation.objects.filter(
            instructor_id__in=instructor_ids,
            camp__area__iexact=camp.area,
            status__in=(
                InstructorAllocation.Status.DRAFT,
                InstructorAllocation.Status.PUBLISHED,
            ),
        ).values_list("instructor_id", flat=True)
    )


def recommendation_cards(need, *, week_start=None, instructors=None):
    """Build transparent staff recommendations for one planning need."""

    week_start = planner_week(week_start or need.requested_start_date)
    week_end = week_start + timedelta(days=6)
    start_date = need.requested_start_date or week_start
    end_date = need.requested_end_date or start_date
    instructors = list(
        instructors
        or Instructor.objects.filter(active=True)
        .select_related("team")
        .prefetch_related("roles")
        .order_by("name_english")
    )
    weekly, recent = _workload_counts(instructors, week_start, week_end)
    area_matches = _area_experience(instructors, need.camp)
    results = []
    for instructor in instructors:
        reasons = []
        conflicts = allocation_conflicts(
            instructor,
            start_date,
            end_date,
            exclude_need=need,
        )
        role_match = not need.required_role_id or instructor.roles.filter(
            pk=need.required_role_id,
            active=True,
        ).exists()
        team_match = not need.preferred_team_id or (
            instructor.team_id == need.preferred_team_id
        )
        if not role_match:
            reasons.append(f"Does not hold {need.required_role.name}")
        if not team_match:
            reasons.append(f"Not in {need.preferred_team.name}")
        if conflicts:
            reasons.append("Date conflict")
        eligible = role_match and team_match and not conflicts
        # Higher is better. Workload has deliberately more weight than prior
        # area experience, so an often-used local instructor is not repeatedly
        # selected over an available colleague.
        score = 100 - (weekly.get(instructor.id, 0) * 20) - (
            recent.get(instructor.id, 0) * 4
        )
        if instructor.id in area_matches:
            score += 6
        if instructor.team_id and need.preferred_team_id == instructor.team_id:
            score += 4
        if not eligible:
            score = -1000 - weekly.get(instructor.id, 0)
        results.append(
            SimpleNamespace(
                instructor=instructor,
                eligible=eligible,
                score=score,
                conflicts=conflicts,
                reasons=reasons,
                weekly_workload=weekly.get(instructor.id, 0),
                recent_workload=recent.get(instructor.id, 0),
                area_match=instructor.id in area_matches,
                role_match=role_match,
                team_match=team_match,
            )
        )
    results.sort(
        key=lambda result: (
            not result.eligible,
            -result.score,
            result.instructor.name_english.casefold(),
        )
    )
    return results


def camp_pipeline(camps=None):
    """Return compact, human-readable camp queue data for the planner."""

    camps = list(
        camps
        or Camp.objects.filter(active=True)
        .prefetch_related("contacts", "course_sessions", "j35_training_needs")
        .order_by("area", "name")
    )
    today = timezone.localdate()
    result = []
    for camp in camps:
        contacts = [contact for contact in camp.contacts.all() if contact.active]
        primary_contact = next(
            (contact for contact in contacts if contact.is_primary),
            contacts[0] if contacts else None,
        )
        needs = list(camp.j35_training_needs.all())
        contact_dates = [need.contacted_at for need in needs if need.contacted_at]
        last_contact_at = max(contact_dates) if contact_dates else None
        course_dates = [
            session.start_date
            for session in camp.course_sessions.all()
            if session.start_date and session.status != CourseSession.Status.CANCELLED
        ]
        last_course_date = max(course_dates) if course_dates else None
        open_need = next(
            (
                need
                for need in sorted(
                    needs,
                    key=lambda item: (item.requested_start_date or date.max, item.id),
                )
                if need.status in OPEN_NEED_STATUSES
            ),
            None,
        )
        result.append(
            SimpleNamespace(
                camp=camp,
                primary_contact=primary_contact,
                last_contact_at=last_contact_at,
                last_course_date=last_course_date,
                open_need=open_need,
                recency_days=(today - last_contact_at.date()).days
                if last_contact_at
                else None,
            )
        )
    return sorted(
        result,
        key=lambda item: (
            item.open_need is None,
            item.last_contact_at is not None,
            item.last_contact_at or timezone.now(),
            item.camp.name.casefold(),
        ),
    )


def create_training_need(*, cleaned_data, user, request):
    """Create a camp outreach or client request and audit the event."""

    source = cleaned_data["source"]
    status = (
        J35TrainingNeed.Status.REQUESTED
        if source == J35TrainingNeed.Source.CLIENT_REQUEST
        else J35TrainingNeed.Status.CONTACTED
    )
    contacted_at = timezone.now()
    need = J35TrainingNeed.objects.create(
        camp=cleaned_data["camp"],
        contact=cleaned_data.get("contact"),
        course=cleaned_data.get("course"),
        source=source,
        status=status,
        priority=cleaned_data["priority"],
        requested_start_date=cleaned_data.get("requested_start_date"),
        requested_end_date=cleaned_data.get("requested_end_date"),
        expected_capacity=cleaned_data.get("expected_capacity"),
        required_instructors=cleaned_data.get("required_instructors") or 1,
        preferred_team=cleaned_data.get("preferred_team"),
        required_role=cleaned_data.get("required_role"),
        notes=cleaned_data.get("notes", ""),
        contact_notes=cleaned_data.get("contact_notes", ""),
        contacted_at=contacted_at,
        created_by=user,
        updated_by=user,
    )
    ActivityLog.objects.create(
        actor=user,
        action=ActivityLog.Action.CREATE,
        object_type="J35TrainingNeed",
        object_id=str(need.public_id),
        description="J35 camp training need recorded.",
        details={
            "camp": need.camp.name,
            "source": need.source,
            "status": need.status,
            "requested_start_date": (
                need.requested_start_date.isoformat()
                if need.requested_start_date
                else ""
            ),
        },
        ip_address=get_client_ip(request),
    )
    return need


def _unique_reference_code(course, camp, start_date):
    base = safe_reference_code(course, camp, start_date)
    candidate = base
    suffix = 2
    while CourseSession.objects.filter(reference_code=candidate).exists():
        suffix_text = f"-{suffix}"
        candidate = f"{base[: 100 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def _notification_message(need, session):
    contact = need.contact
    contact_text = ""
    if contact:
        contact_text = f" Point of contact: {contact.name}."
    return (
        f"You have been allocated to {session.course.title_english} at "
        f"{session.camp.name} from {session.start_date:%d %b} to "
        f"{(session.end_date or session.start_date):%d %b}."
        f"{contact_text}"
    )


def confirm_training_need(*, need, instructor_ids, override_reason, user, request):
    """Confirm a J35 need as a real course and notify the selected trainers.

    This is intentionally one transactional boundary.  A failed validation
    leaves no half-created CourseSession, CourseInstructor or allocation rows.
    Email is deliberately not sent here: production can add it from a durable
    post-commit worker, while development uses only the in-app Notification.
    """

    instructor_ids = list(dict.fromkeys(instructor_ids))
    if not instructor_ids:
        raise ValidationError("Choose at least one instructor before confirming.")
    override_reason = (override_reason or "").strip()

    with transaction.atomic():
        locked_need = J35TrainingNeed.objects.select_for_update(of=("self",)).select_related(
            "camp", "contact", "course", "proposed_session"
        ).get(pk=need.pk)
        if locked_need.status == J35TrainingNeed.Status.CONFIRMED:
            raise ValidationError("This training need has already been confirmed.")
        if locked_need.status not in OPEN_NEED_STATUSES:
            raise ValidationError("Only an open J35 training need can be confirmed.")
        if not locked_need.course_id:
            raise ValidationError("Select a course before confirming this training need.")
        if not locked_need.requested_start_date:
            raise ValidationError(
                "Set the requested course start date before confirming this training need."
            )
        end_date = locked_need.requested_end_date or locked_need.requested_start_date
        if end_date < locked_need.requested_start_date:
            raise ValidationError("The requested end date cannot be before the start date.")
        # Lock selected trainers in a stable order before checking availability.
        # This prevents two planners from confirming competing needs for the
        # same person and dates at the same time on databases that support row
        # locking (including production PostgreSQL).
        instructors = list(
            Instructor.objects.select_for_update(of=("self",))
            .filter(id__in=instructor_ids, active=True)
            .select_related("team", "user")
            .prefetch_related("roles")
            .order_by("pk")
        )
        if len(instructors) != len(instructor_ids):
            raise ValidationError(
                "One or more selected instructors are no longer active."
            )
        if len(instructors) < locked_need.required_instructors:
            raise ValidationError(
                f"This need requires at least {locked_need.required_instructors} trainer"
                f"{'s' if locked_need.required_instructors != 1 else ''}."
            )

        # Recompute eligibility from the locked, current database state. This
        # makes the override decision server-side and protects against stale
        # planner pages or a forged client-side selection.
        recommendations = {
            card.instructor.id: card
            for card in recommendation_cards(locked_need, instructors=instructors)
        }
        selected_conflicts = []
        for instructor in instructors:
            card = recommendations[instructor.id]
            if not card.eligible:
                selected_conflicts.append(
                    f"{instructor.name_english}: " + "; ".join(card.reasons)
                )
        if selected_conflicts and not override_reason:
            raise ValidationError(
                "A manual override reason is required: "
                + "; ".join(selected_conflicts)
            )

        session = locked_need.proposed_session
        poc = locked_need.contact
        if session is None:
            session = CourseSession.objects.create(
                course=locked_need.course,
                camp=locked_need.camp,
                start_date=locked_need.requested_start_date,
                end_date=locked_need.requested_end_date
                or locked_need.requested_start_date,
                capacity=locked_need.expected_capacity,
                reference_code=_unique_reference_code(
                    locked_need.course,
                    locked_need.camp,
                    locked_need.requested_start_date,
                ),
                sequence_number=allocate_course_sequence_number(),
                status=CourseSession.Status.REGISTRATION_CLOSED,
                registration_published=False,
                poc_name=poc.name if poc else "",
                poc_contact_number=poc.phone if poc else "",
                created_by=user,
                confirmed_at=timezone.now(),
                notes=(
                    f"Created from J35 training need {locked_need.public_id}."
                ),
            )
        else:
            if (
                session.status != CourseSession.Status.DRAFT
                or session.registrations.exists()
                or session.instructor_assignments.exists()
            ):
                raise ValidationError(
                    "A linked proposed course must be an empty draft before it can be confirmed through J35."
                )
            session.course = locked_need.course
            session.camp = locked_need.camp
            session.start_date = locked_need.requested_start_date
            session.end_date = locked_need.requested_end_date or locked_need.requested_start_date
            session.capacity = locked_need.expected_capacity
            if poc:
                session.poc_name = poc.name
                session.poc_contact_number = poc.phone
            session.save(
                update_fields=[
                    "course",
                    "camp",
                    "start_date",
                    "end_date",
                    "capacity",
                    "poc_name",
                    "poc_contact_number",
                    "updated_at",
                ]
            )

        notifications = 0
        allocation_ids = []
        for instructor in instructors:
            assignment, _ = CourseInstructor.objects.get_or_create(
                session=session,
                instructor=instructor,
                defaults={
                    "assignment_role": CourseInstructor.AssignmentRole.INSTRUCTOR,
                },
            )
            allocation, _ = InstructorAllocation.objects.update_or_create(
                training_need=locked_need,
                instructor=instructor,
                defaults={
                    "session": session,
                    "camp": locked_need.camp,
                    "activity": locked_need.course.title_english,
                    "start_date": locked_need.requested_start_date,
                    "end_date": end_date,
                    "status": InstructorAllocation.Status.PUBLISHED,
                    "allocation_kind": InstructorAllocation.AllocationKind.COURSE,
                    "override_reason": override_reason,
                    "updated_by": user,
                    "created_by": user,
                },
            )
            # The model continues to protect against accidental conflicts for
            # all old direct-allocation forms.  Override is explicit here.
            try:
                allocation.full_clean()
            except ValidationError as exc:
                raise ValidationError(exc.messages) from exc
            allocation.save()
            allocation_ids.append(str(allocation.public_id))
            if instructor.user_id and instructor.user.is_active:
                Notification.objects.create(
                    recipient=instructor.user,
                    category=Notification.Category.INFORMATION,
                    title="J35 training allocation confirmed",
                    message=_notification_message(locked_need, session),
                    link=reverse(
                        "instructor_course_workspace",
                        kwargs={"public_id": session.public_id},
                    ),
                )
                notified_at = timezone.now()
                allocation.notified_at = notified_at
                allocation.save(update_fields=["notified_at", "updated_at"])
                if assignment.notified_at is None:
                    assignment.notified_at = notified_at
                    assignment.save(update_fields=["notified_at", "updated_at"])
                notifications += 1

        locked_need.proposed_session = session
        locked_need.status = J35TrainingNeed.Status.CONFIRMED
        locked_need.confirmed_by = user
        locked_need.confirmed_at = timezone.now()
        locked_need.updated_by = user
        locked_need.save(
            update_fields=[
                "proposed_session",
                "status",
                "confirmed_by",
                "confirmed_at",
                "updated_by",
                "updated_at",
            ]
        )
        ActivityLog.objects.create(
            actor=user,
            action=ActivityLog.Action.APPROVE,
            object_type="J35TrainingNeed",
            object_id=str(locked_need.public_id),
            description="J35 training need confirmed and trainers notified.",
            details={
                "camp": locked_need.camp.name,
                "course_session": str(session.public_id),
                "instructors": [instructor.name_english for instructor in instructors],
                "allocation_ids": allocation_ids,
                "notification_count": notifications,
                "manual_override_reason": override_reason,
                "conflicts": selected_conflicts,
            },
            ip_address=get_client_ip(request),
        )
    return session, notifications, bool(selected_conflicts)
