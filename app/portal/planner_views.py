"""The native J35 instructor allocation planner."""

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .access import (
    can_manage_j35_planner,
    can_view_j35_planner,
    instructor_for_user,
)
from .course_services import display_course_name
from .forms import CampContactForm, J35AllocationForm, J35TrainingNeedForm
from .j35_services import (
    OPEN_NEED_STATUSES,
    allocation_conflicts,
    camp_pipeline,
    confirm_training_need,
    create_training_need,
    recommendation_cards,
)
from .models import (
    ActivityLog,
    Camp,
    CampContact,
    Course,
    CourseInstructor,
    CourseSession,
    Instructor,
    InstructorAllocation,
    InstructorRole,
    J35TrainingNeed,
    Notification,
    Team,
)
from .security import get_client_ip


def _planner_start(value):
    try:
        requested = date.fromisoformat(value)
    except (TypeError, ValueError):
        requested = timezone.localdate()
    return requested - timedelta(days=requested.weekday())


def _query_values(request, manager, current_instructor):
    team = request.GET.get("team", "").strip()
    instructor = request.GET.get("instructor", "").strip()
    camp = request.GET.get("camp", "").strip()
    activity = request.GET.get("activity", "").strip()[:100]
    status = request.GET.get("status", "").strip()
    if not manager:
        team = instructor = camp = activity = ""
        status = InstructorAllocation.Status.PUBLISHED
    if not team.isdigit():
        team = ""
    if not instructor.isdigit():
        instructor = ""
    if not camp.isdigit():
        camp = ""
    if status not in {"", *InstructorAllocation.Status.values}:
        status = ""
    return {
        "team": team,
        "instructor": instructor,
        "camp": camp,
        "activity": activity,
        "status": status,
        "current_instructor": current_instructor,
    }


def _allocation_queryset(start, end, filters):
    allocations = InstructorAllocation.objects.select_related(
        "instructor",
        "instructor__team",
        "session",
        "session__course",
        "camp",
        "created_by",
        "updated_by",
    ).filter(start_date__lte=end, end_date__gte=start)
    if filters["current_instructor"] is not None:
        allocations = allocations.filter(
            instructor=filters["current_instructor"],
            status=InstructorAllocation.Status.PUBLISHED,
        )
    else:
        if filters["team"]:
            allocations = allocations.filter(instructor__team_id=filters["team"])
        if filters["instructor"]:
            allocations = allocations.filter(instructor_id=filters["instructor"])
        if filters["camp"]:
            allocations = allocations.filter(camp_id=filters["camp"])
        if filters["activity"]:
            allocations = allocations.filter(activity__icontains=filters["activity"])
        if filters["status"]:
            allocations = allocations.filter(status=filters["status"])
    return allocations.order_by("start_date", "end_date", "instructor__name_english")


def _save_allocation(request, allocation=None):
    form = J35AllocationForm(request.POST, instance=allocation)
    if not form.is_valid():
        return form
    candidate = form.save(commit=False)
    allocation_kind = (
        request.POST.get("allocation_kind")
        or (
            InstructorAllocation.AllocationKind.COURSE
            if candidate.session_id
            else InstructorAllocation.AllocationKind.OTHER
        )
    )
    if allocation_kind not in InstructorAllocation.AllocationKind.values:
        form.add_error(None, "Choose a valid allocation type.")
        return form
    candidate.allocation_kind = allocation_kind
    candidate.override_reason = (request.POST.get("override_reason") or "").strip()
    if candidate.session_id and not candidate.camp_id:
        candidate.camp = candidate.session.camp
    if allocation is None:
        candidate.created_by = request.user
    candidate.updated_by = request.user
    try:
        candidate.full_clean()
    except ValidationError as exc:
        form.add_error(None, exc)
        return form
    with transaction.atomic():
        candidate.save()
        ActivityLog.objects.create(
            actor=request.user,
            action=(
                ActivityLog.Action.CREATE
                if allocation is None
                else ActivityLog.Action.UPDATE
            ),
            object_type="InstructorAllocation",
            object_id=str(candidate.public_id),
            description=(
                "J35 allocation created."
                if allocation is None
                else "J35 allocation updated."
            ),
            details={
                "instructor": candidate.instructor.name_english,
                "activity": candidate.activity,
                "start_date": candidate.start_date.isoformat(),
                "end_date": candidate.end_date.isoformat(),
                "status": candidate.status,
                "allocation_kind": candidate.allocation_kind,
                "manual_override_reason": candidate.override_reason,
            },
            ip_address=get_client_ip(request),
        )
    return None


def _create_pipeline_allocations(request):
    """Create one course-linked allocation per selected staff member.

    The course pipeline always takes its dates and camp from the selected
    course session.  All proposed allocations are validated before anything
    is saved, so a conflict cannot leave a partially created course plan.
    """

    session_id = (request.POST.get("session_id") or "").strip()
    if not session_id.isdigit():
        return ["Select a course session before saving the plan."]
    session = get_object_or_404(
        CourseSession.objects.exclude(status=CourseSession.Status.CANCELLED)
        .select_related("course", "camp"),
        pk=session_id,
    )
    if not session.start_date:
        return ["The selected course needs a start date before it can be planned."]

    raw_instructor_ids = request.POST.getlist("instructor_ids")
    instructor_ids = list(
        dict.fromkeys(value for value in raw_instructor_ids if value.isdigit())
    )
    if not instructor_ids:
        return ["Choose at least one active instructor for this course."]

    instructors = list(
        Instructor.objects.filter(id__in=instructor_ids, active=True)
        .select_related("team", "user")
        .order_by("name_english")
    )
    if len(instructors) != len(instructor_ids):
        return ["One or more selected instructors are no longer active."]

    status = request.POST.get("status", InstructorAllocation.Status.DRAFT)
    if status not in {
        InstructorAllocation.Status.DRAFT,
        InstructorAllocation.Status.PUBLISHED,
    }:
        return ["Choose Draft or Published for the course allocation."]

    activity = (request.POST.get("activity") or "").strip()[:200]
    if not activity:
        activity = session.course.title_english or session.course.code or "Course"
    notes = (request.POST.get("notes") or "").strip()
    end_date = session.end_date or session.start_date
    candidates = [
        InstructorAllocation(
            instructor=instructor,
            session=session,
            camp=session.camp,
            activity=activity,
            start_date=session.start_date,
            end_date=end_date,
            status=status,
            allocation_kind=InstructorAllocation.AllocationKind.COURSE,
            notes=notes,
            created_by=request.user,
            updated_by=request.user,
        )
        for instructor in instructors
    ]

    errors = []
    for candidate in candidates:
        try:
            candidate.full_clean()
        except ValidationError as exc:
            errors.extend(
                f"{candidate.instructor.name_english}: {message}"
                for message in exc.messages
            )
        course_conflicts = allocation_conflicts(
            candidate.instructor,
            candidate.start_date,
            candidate.end_date,
            exclude_session_id=session.id,
        )
        if course_conflicts:
            errors.append(
                f"{candidate.instructor.name_english}: "
                "already assigned to "
                + "; ".join(course_conflicts)
            )
    if errors:
        return errors

    with transaction.atomic():
        for candidate in candidates:
            candidate.save()
            if status == InstructorAllocation.Status.PUBLISHED:
                assignment, assignment_created = CourseInstructor.objects.get_or_create(
                    session=session,
                    instructor=candidate.instructor,
                    defaults={
                        "assignment_role": CourseInstructor.AssignmentRole.INSTRUCTOR,
                    },
                )
                notification_link = reverse(
                    "instructor_course_workspace",
                    kwargs={"public_id": session.public_id},
                )
                notification_exists = Notification.objects.filter(
                    recipient=candidate.instructor.user,
                    title="J35 course allocation confirmed",
                    link=notification_link,
                ).exists() if candidate.instructor.user_id else False
                if (
                    candidate.instructor.user_id
                    and candidate.instructor.user.is_active
                    and not notification_exists
                ):
                    Notification.objects.create(
                        recipient=candidate.instructor.user,
                        category=Notification.Category.INFORMATION,
                        title="J35 course allocation confirmed",
                        message=(
                            f"You have been allocated to {session.course.title_english} "
                            f"at {session.camp or 'the assigned camp'} from "
                            f"{session.start_date:%d %b} to {end_date:%d %b}."
                        ),
                        link=notification_link,
                    )
                    notified_at = timezone.now()
                    candidate.notified_at = notified_at
                    candidate.save(update_fields=["notified_at", "updated_at"])
                    if assignment.notified_at is None:
                        assignment.notified_at = notified_at
                        assignment.save(update_fields=["notified_at", "updated_at"])
            ActivityLog.objects.create(
                actor=request.user,
                action=ActivityLog.Action.CREATE,
                object_type="InstructorAllocation",
                object_id=str(candidate.public_id),
                description="J35 course allocation created from the planning pipeline.",
                details={
                    "source": "course_pipeline",
                    "instructor": candidate.instructor.name_english,
                    "course_session": str(session.public_id),
                    "activity": candidate.activity,
                    "start_date": candidate.start_date.isoformat(),
                    "end_date": candidate.end_date.isoformat(),
                    "status": candidate.status,
                    "notification_created": bool(candidate.notified_at),
                },
                ip_address=get_client_ip(request),
            )
    return []


def _pipeline_sessions(start, end):
    sessions = list(
        CourseSession.objects.exclude(status=CourseSession.Status.CANCELLED)
        .filter(start_date__isnull=False, start_date__lte=end)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
        .select_related("course", "camp")
        .order_by("start_date", "course__title_english")
    )
    for session in sessions:
        session.planner_label = (
            f"{display_course_name(session)} · "
            f"{session.camp.name if session.camp_id else 'Camp not set'} · "
            f"{session.start_date:%d %b}"
        )
    return sessions


def _training_need_form_from_post(post_data, instance=None):
    """Accept compact planner fields without accepting lifecycle controls.

    The planner model form intentionally excludes lifecycle fields.  They are
    set only by the server-side confirmation workflow, never by a browser.
    """

    data = post_data.copy()
    field_aliases = {
        "camp_id": "camp",
        "contact_id": "contact",
        "course_id": "course",
        "start_date": "requested_start_date",
        "end_date": "requested_end_date",
        "request_type": "source",
    }
    for incoming_name, model_name in field_aliases.items():
        if incoming_name in data and model_name not in data:
            data[model_name] = data.get(incoming_name)

    source = (data.get("source") or "").strip().lower()
    if source in {"request", "client_request", "client request"}:
        data["source"] = J35TrainingNeed.Source.CLIENT_REQUEST
    else:
        data["source"] = J35TrainingNeed.Source.OUTREACH
    if not data.get("required_instructors"):
        data["required_instructors"] = "1"
    if instance is not None and "contact_notes" not in data:
        # The compact edit form does not expose historic call notes.  Keep
        # them intact while allowing the planner to change the current need.
        data["contact_notes"] = instance.contact_notes
    return J35TrainingNeedForm(data, instance=instance)


def _camp_contact_form_from_post(post_data, instance=None):
    data = post_data.copy()
    if "camp_id" in data and "camp" not in data:
        data["camp"] = data.get("camp_id")
    return CampContactForm(data, instance=instance)


def _save_camp_contact(*, form, user, request):
    """Save a J35 camp contact while preserving exactly one primary contact."""

    creating = form.instance.pk is None
    candidate = form.save(commit=False)
    with transaction.atomic():
        if candidate.is_primary:
            CampContact.objects.filter(
                camp=candidate.camp,
                is_primary=True,
            ).exclude(pk=candidate.pk).update(is_primary=False)
        try:
            candidate.full_clean()
        except ValidationError as exc:
            transaction.set_rollback(True)
            return exc.messages
        candidate.save()
        ActivityLog.objects.create(
            actor=user,
            action=(
                ActivityLog.Action.CREATE
                if creating
                else ActivityLog.Action.UPDATE
            ),
            object_type="CampContact",
            object_id=str(candidate.pk),
            description=(
                "J35 camp point of contact added."
                if creating
                else "J35 camp point of contact updated."
            ),
            details={
                "camp": candidate.camp.name,
                "contact": candidate.name,
                "is_primary": candidate.is_primary,
            },
            ip_address=get_client_ip(request),
        )
    return []


@login_required
@require_http_methods(["GET", "POST"])
def j35_planner(request):
    if not can_view_j35_planner(request.user):
        raise PermissionDenied("J35 planner access is limited to assigned staff.")
    manager = can_manage_j35_planner(request.user)
    current_instructor = None if manager else instructor_for_user(request.user)
    if not manager and current_instructor is None:
        raise PermissionDenied("This account has no staff profile for the J35 planner.")

    if request.method == "POST":
        if not manager:
            raise PermissionDenied("Only assigned J35 planners may change allocations.")
        action = request.POST.get("action", "")
        if action == "create_contact":
            form = _camp_contact_form_from_post(request.POST)
            if form.is_valid():
                errors = _save_camp_contact(
                    form=form,
                    user=request.user,
                    request=request,
                )
                if errors:
                    for error in errors:
                        messages.error(request, error)
                else:
                    messages.success(request, "Camp point of contact added.")
            else:
                for field_errors in form.errors.values():
                    for error in field_errors:
                        messages.error(request, error)
        elif action == "update_contact":
            contact = get_object_or_404(
                CampContact,
                pk=request.POST.get("contact_id"),
            )
            form = _camp_contact_form_from_post(request.POST, instance=contact)
            if form.is_valid():
                errors = _save_camp_contact(
                    form=form,
                    user=request.user,
                    request=request,
                )
                if errors:
                    for error in errors:
                        messages.error(request, error)
                else:
                    messages.success(request, "Camp point of contact updated.")
            else:
                for field_errors in form.errors.values():
                    for error in field_errors:
                        messages.error(request, error)
        elif action == "create_need":
            form = _training_need_form_from_post(request.POST)
            if form.is_valid():
                create_training_need(
                    cleaned_data=form.cleaned_data,
                    user=request.user,
                    request=request,
                )
                messages.success(request, "Camp training need added to the J35 pipeline.")
            else:
                for field_errors in form.errors.values():
                    for error in field_errors:
                        messages.error(request, error)
        elif action == "update_need":
            need = get_object_or_404(
                J35TrainingNeed,
                public_id=request.POST.get("need_id"),
            )
            if need.status == J35TrainingNeed.Status.CONFIRMED:
                messages.error(
                    request,
                    "This confirmed training need is locked. Record any change through the approved course workflow.",
                )
            else:
                form = _training_need_form_from_post(request.POST, instance=need)
                if form.is_valid():
                    candidate = form.save(commit=False)
                    candidate.updated_by = request.user
                    try:
                        candidate.full_clean()
                    except ValidationError as exc:
                        for error in exc.messages:
                            messages.error(request, error)
                    else:
                        candidate.save()
                        ActivityLog.objects.create(
                            actor=request.user,
                            action=ActivityLog.Action.UPDATE,
                            object_type="J35TrainingNeed",
                            object_id=str(candidate.public_id),
                            description="J35 training need updated.",
                            details={
                                "camp": candidate.camp.name,
                                "status": candidate.status,
                            },
                            ip_address=get_client_ip(request),
                        )
                        messages.success(request, "J35 training need updated.")
                else:
                    for field_errors in form.errors.values():
                        for error in field_errors:
                            messages.error(request, error)
        elif action == "confirm_need":
            need = get_object_or_404(
                J35TrainingNeed.objects.select_related("camp", "contact", "course"),
                public_id=request.POST.get("need_id"),
            )
            instructor_ids = [
                int(value)
                for value in request.POST.getlist("instructor_ids")
                if value.isdigit()
            ]
            try:
                session, notifications, overridden = confirm_training_need(
                    need=need,
                    instructor_ids=instructor_ids,
                    override_reason=request.POST.get("override_reason", ""),
                    user=request.user,
                    request=request,
                )
            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, error)
            else:
                override_text = " Manual override recorded." if overridden else ""
                messages.success(
                    request,
                    (
                        f"Training confirmed for {session.camp.name}; "
                        f"{notifications} trainer notification"
                        f"{'s' if notifications != 1 else ''} created."
                        f"{override_text}"
                    ),
                )
        elif action == "create":
            form_error = _save_allocation(request)
            if form_error:
                for field_errors in form_error.errors.values():
                    for error in field_errors:
                        messages.error(request, error)
            else:
                messages.success(request, "J35 allocation saved.")
        elif action == "pipeline_create":
            errors = _create_pipeline_allocations(request)
            if errors:
                for error in errors:
                    messages.error(request, error)
            else:
                messages.success(request, "Course allocation plan saved.")
        elif action == "update":
            allocation = get_object_or_404(
                InstructorAllocation.objects.select_related("training_need"),
                public_id=request.POST.get("allocation_id"),
            )
            if allocation.training_need_id:
                messages.error(
                    request,
                    "This confirmed training allocation is managed by the J35 pipeline and cannot be edited here.",
                )
            else:
                form_error = _save_allocation(request, allocation=allocation)
                if form_error:
                    for field_errors in form_error.errors.values():
                        for error in field_errors:
                            messages.error(request, error)
                else:
                    messages.success(request, "J35 allocation updated.")
        elif action == "cancel":
            allocation = get_object_or_404(
                InstructorAllocation.objects.select_related("training_need"),
                public_id=request.POST.get("allocation_id"),
            )
            if allocation.training_need_id:
                messages.error(
                    request,
                    "This confirmed training allocation is managed by the J35 pipeline and cannot be cancelled here.",
                )
            elif allocation.status != InstructorAllocation.Status.CANCELLED:
                allocation.status = InstructorAllocation.Status.CANCELLED
                allocation.updated_by = request.user
                allocation.save(update_fields=["status", "updated_by", "updated_at"])
                ActivityLog.objects.create(
                    actor=request.user,
                    action=ActivityLog.Action.CANCEL,
                    object_type="InstructorAllocation",
                    object_id=str(allocation.public_id),
                    description="J35 allocation cancelled.",
                    details={"activity": allocation.activity},
                    ip_address=get_client_ip(request),
                )
                messages.success(request, "J35 allocation cancelled.")
            else:
                messages.info(request, "This J35 allocation was already cancelled.")
        else:
            messages.error(request, "Choose a valid J35 planner action.")
        return redirect(request.get_full_path())

    start = _planner_start(request.GET.get("start", ""))
    end = start + timedelta(days=27)
    filters = _query_values(request, manager, current_instructor)
    allocations = list(_allocation_queryset(start, end, filters))
    weeks = []
    for week_number, week_offset in enumerate(range(0, 28, 7), start=1):
        week_start = start + timedelta(days=week_offset)
        week_end = week_start + timedelta(days=6)
        week_allocations = [
            allocation
            for allocation in allocations
            if allocation.start_date <= week_end
            and allocation.end_date >= week_start
        ]
        for allocation in week_allocations:
            allocation.planner_form_id = (
                f"allocation-{allocation.public_id}-week-{week_number}"
            )
        weeks.append(
            {
                "start": week_start,
                "end": week_end,
                "allocations": week_allocations,
            }
        )
    active_instructors = list(
        Instructor.objects.filter(active=True)
        .select_related("team", "user")
        .prefetch_related("roles")
        .order_by("name_english")
    )
    open_needs = []
    camp_queue = []
    if manager:
        need_date_filter = Q(requested_start_date__isnull=True) | Q(
            requested_start_date__lte=end
        )
        open_needs = list(
            J35TrainingNeed.objects.filter(
                status__in=OPEN_NEED_STATUSES,
            )
            .filter(need_date_filter)
            .annotate(
                priority_order=Case(
                    When(
                        priority=J35TrainingNeed.Priority.URGENT,
                        then=Value(0),
                    ),
                    When(
                        priority=J35TrainingNeed.Priority.HIGH,
                        then=Value(1),
                    ),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            )
            .select_related(
                "camp",
                "contact",
                "course",
                "preferred_team",
                "required_role",
            )
            .order_by(
                "priority_order",
                "requested_start_date",
                "created_at",
            )
        )
        for need in open_needs:
            recommendations = recommendation_cards(
                need,
                week_start=start,
                instructors=active_instructors,
            )
            for recommendation in recommendations:
                recommendation.reason = "; ".join(recommendation.reasons)
                if not recommendation.reason and recommendation.eligible:
                    recommendation.reason = "Available"
            selected_recommendations = [
                recommendation
                for recommendation in recommendations
                if recommendation.eligible
            ][: need.required_instructors]
            suggested_ids = {
                recommendation.instructor.id
                for recommendation in selected_recommendations
            }
            need.recommendations = recommendations
            need.suggested_ids = suggested_ids
            need.conflicts = [
                recommendation
                for recommendation in recommendations
                if recommendation.conflicts or not recommendation.eligible
            ]
            need.weekly_workload = sum(
                recommendation.weekly_workload
                for recommendation in recommendations
                if recommendation.eligible
            )
        camp_queue = camp_pipeline()
    return render(
        request,
        "portal/j35_planner.html",
        {
            "active_module": "j35",
            "can_manage": manager,
            "start": start,
            "week_start": start,
            "week_end": start + timedelta(days=6),
            "previous_start": start - timedelta(days=28),
            "next_start": start + timedelta(days=28),
            "weeks": weeks,
            "filters": filters,
            "teams": Team.objects.filter(active=True),
            "roles": InstructorRole.objects.filter(active=True),
            "courses": Course.objects.filter(active=True),
            "contacts": CampContact.objects.filter(active=True).select_related("camp"),
            "all_contacts": CampContact.objects.select_related("camp").order_by(
                "camp__name", "-is_primary", "name"
            ),
            "instructors": active_instructors,
            "camps": Camp.objects.filter(active=True),
            "sessions": CourseSession.objects.exclude(
                status=CourseSession.Status.CANCELLED
            )
            .select_related("course", "camp")
            .order_by("start_date", "course__title_english"),
            "pipeline_sessions": _pipeline_sessions(start, end),
            "status_choices": InstructorAllocation.Status.choices,
            "form": J35AllocationForm(),
            "need_form": J35TrainingNeedForm(),
            "camp_pipeline": camp_queue,
            "training_needs": open_needs,
            "planner_proposals": open_needs,
        },
    )
