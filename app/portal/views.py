from datetime import date
from urllib.parse import urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import (
    ActivityLog,
    Course,
    CourseSession,
    CourseSessionProposal,
    DataIssue,
    EntityAliasProposal,
    Instructor,
    Notification,
    Registration,
    SourceRecord,
    Student,
    StudentIdentityProposal,
    Team,
    TrainingRecord,
)
from .access import (
    can_view_inventory,
    can_view_j35_planner,
    can_view_dashboard,
    can_view_all_courses,
    can_view_reports,
    instructor_for_user,
    is_operations_admin,
)
from .identity import format_emirates_id
from .dashboard_history import project_historical_dashboard


MODULES = {
    "students": {
        "title": "Students",
        "description":
            "Search master profiles and course history.",
    },
    "training": {
        "title": "Training Records",
        "description":
            "Attendance, assessments, results and duplicate review.",
    },
    "courses": {
        "title": "Courses & Sessions",
        "description":
            "Course dates, instructors, camps, capacity and status.",
    },
    "registrations": {
        "title": "Registrations",
        "description":
            "Public registration links, submissions and approvals.",
    },
    "schedule": {
        "title": "Schedule",
        "description":
            "J35 planning and instructor assignments.",
    },
    "staff": {
        "title": "Staff & Teams",
        "description": "Staff, teams, positions and responsibilities.",
    },
    "inventory": {
        "title": "Inventory",
        "description":
            "Items, issues, returns and consumption.",
    },
    "reports": {
        "title": "Reports & Exports",
        "description":
            "External Excel, stamped lists and print workflows.",
    },
    "data-quality": {
        "title": "Data Quality",
        "description":
            "Duplicate registrations, master-list matches and previous-course review.",
    },
}


def _safe_notification_link(request, link):
    """Expose only same-site, path-relative notification destinations.

    Notification links are normally created with ``reverse()``. This guard
    prevents a malformed legacy/admin value from turning the private inbox
    into an external-link or open-redirect surface.
    """

    link = (link or "").strip()
    if not link or not link.startswith("/") or link.startswith(("//", "/\\")):
        return ""
    parsed = urlsplit(link)
    if parsed.scheme or parsed.netloc:
        return ""
    if not url_has_allowed_host_and_scheme(
        link,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return ""
    return link


@login_required
def notification_inbox(request):
    """Show the current user's notifications and no other user's records."""

    notification_queryset = Notification.objects.filter(recipient=request.user)
    notifications = list(
        notification_queryset.order_by("-created_at", "-id")[:100]
    )
    for notification in notifications:
        notification.safe_link = _safe_notification_link(request, notification.link)

    return render(
        request,
        "portal/notification_inbox.html",
        {
            "notifications": notifications,
            "unread_count": notification_queryset.filter(read_at__isnull=True).count(),
        },
    )


@login_required
@require_POST
def notification_mark_read(request, notification_id):
    """Mark only the requesting user's notification as read."""

    notification = get_object_or_404(
        Notification.objects.filter(recipient=request.user),
        pk=notification_id,
    )
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
        messages.success(request, "Notification marked as read.")

    destination = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(
        destination,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(destination)
    return redirect("notification_inbox")


@login_required
def dashboard(request):
    if not can_view_dashboard(request.user):
        return redirect("my_courses")
    today = timezone.localdate()

    def requested_date(name, default):
        value = request.GET.get(name, "").strip()
        if not value:
            return default
        try:
            return date.fromisoformat(value)
        except ValueError:
            return default

    date_from = requested_date("date_from", date(today.year, 1, 1))
    date_to = requested_date("date_to", date(today.year, 12, 31))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    all_history = request.GET.get("all_history", "").strip() == "1"
    selected_course = request.GET.get("course", "").strip()
    selected_team = request.GET.get("team", "").strip()
    selected_instructor = request.GET.get("instructor", "").strip()
    administrator = can_view_all_courses(request.user) or can_view_reports(request.user)
    instructor = instructor_for_user(request.user)
    sessions = CourseSession.objects.select_related("course", "camp").exclude(
        status__in=[CourseSession.Status.DRAFT, CourseSession.Status.CANCELLED],
    )
    if not administrator:
        if instructor is None:
            sessions = sessions.none()
        else:
            sessions = sessions.filter(
                instructor_assignments__instructor=instructor
            )
    if selected_course.isdigit():
        sessions = sessions.filter(course_id=int(selected_course))
    else:
        selected_course = ""
    if selected_team.isdigit():
        sessions = sessions.filter(
            instructor_assignments__instructor__team_id=int(selected_team)
        )
    else:
        selected_team = ""
    if selected_instructor.isdigit():
        sessions = sessions.filter(
            instructor_assignments__instructor_id=int(selected_instructor)
        )
    else:
        selected_instructor = ""
    if not all_history:
        sessions = sessions.filter(
            start_date__lte=date_to,
        ).filter(
            Q(end_date__gte=date_from)
            | Q(end_date__isnull=True, start_date__gte=date_from),
        )
    sessions = sessions.distinct()

    registrations = Registration.objects.filter(requested_session__in=sessions)
    selected_registrations = registrations.filter(
        selected_for_roster=True,
    ).exclude(
        status=Registration.Status.REJECTED,
    ).exclude(
        duplicate_review_status=(
            Registration.DuplicateReviewStatus.EXCLUDED
        )
    )
    training_records = TrainingRecord.objects.filter(
        session__in=sessions,
        duplicate_flag=False,
    ).exclude(
        record_status=TrainingRecord.RecordStatus.CANCELLED,
    )

    training_by_participation = {}
    for record in training_records.order_by("updated_at", "id").values(
        "id",
        "student_id",
        "session_id",
        "session__start_date",
        "result",
        "is_hp",
        "is_ttt",
        "raw_payload",
        "updated_at",
    ):
        training_by_participation[
            (record["student_id"], record["session_id"])
        ] = record
    training_rows = list(training_by_participation.values())
    represented_registration_ids = set()
    represented_student_sessions = set()
    for record in training_rows:
        payload = record["raw_payload"]
        if isinstance(payload, dict):
            registration_id = payload.get("registration_public_id")
            if registration_id:
                represented_registration_ids.add(str(registration_id))
        if record["student_id"]:
            represented_student_sessions.add(
                (record["student_id"], record["session_id"])
            )

    current_by_participation = {}
    for registration in selected_registrations.order_by(
        "updated_at", "id"
    ).values(
        "id",
        "public_id",
        "student_id",
        "requested_session_id",
        "requested_session__start_date",
        "assessment_status",
        "is_hp",
        "is_ttt",
        "updated_at",
    ):
        represented_by_id = (
            str(registration["public_id"]) in represented_registration_ids
        )
        represented_by_student = (
            registration["student_id"] is not None
            and (
                registration["student_id"],
                registration["requested_session_id"],
            )
            in represented_student_sessions
        )
        if represented_by_id or represented_by_student:
            continue
        if registration["student_id"] is None:
            key = ("registration", registration["id"])
        else:
            key = (
                "student_session",
                registration["student_id"],
                registration["requested_session_id"],
            )
        current_by_participation[key] = registration
    current_registration_rows = list(current_by_participation.values())

    rejected_filter = Q(status=Registration.Status.REJECTED) | Q(
        duplicate_review_status=Registration.DuplicateReviewStatus.EXCLUDED
    )
    rejected = registrations.filter(rejected_filter).distinct()

    totals = {
        "courses": sessions.count(),
        "enrolled": len(training_rows) + len(current_registration_rows),
        "passed": sum(
            record["result"] == TrainingRecord.Result.PASS
            for record in training_rows
        )
        + sum(
            registration["assessment_status"] == "passed"
            for registration in current_registration_rows
        ),
        "failed": sum(
            record["result"] == TrainingRecord.Result.FAIL
            for record in training_rows
        )
        + sum(
            registration["assessment_status"] == "failed"
            for registration in current_registration_rows
        ),
        "rejected": rejected.count(),
        "hp": sum(record["is_hp"] for record in training_rows)
        + sum(
            registration["is_hp"]
            for registration in current_registration_rows
        ),
        "ttt": sum(record["is_ttt"] for record in training_rows)
        + sum(
            registration["is_ttt"]
            for registration in current_registration_rows
        ),
    }

    historical_projection = project_historical_dashboard(
        administrator=administrator,
        instructor=instructor,
        date_from=date_from,
        date_to=date_to,
        all_history=all_history,
        selected_course=selected_course,
        selected_team=selected_team,
        selected_instructor=selected_instructor,
    )
    # Historical course proposals remain proposals until an administrator
    # approves them.  Their dated student evidence can be reported without
    # inflating the confirmed Course Sessions counter.
    for key in ("enrolled", "passed", "failed", "rejected", "hp", "ttt"):
        totals[key] += historical_projection.totals[key]

    for key, value in list(totals.items()):
        totals[f"{key}_digits"] = f"{value:04d}"

    month_values = {
        month: {"enrolled": 0, "passed": 0, "failed": 0, "rejected": 0}
        for month in range(1, 13)
    }
    for record in training_rows:
        session_date = record["session__start_date"]
        if session_date is None:
            continue
        month = month_values[session_date.month]
        month["enrolled"] += 1
        if record["result"] == TrainingRecord.Result.PASS:
            month["passed"] += 1
        elif record["result"] == TrainingRecord.Result.FAIL:
            month["failed"] += 1

    for registration in current_registration_rows:
        session_date = registration["requested_session__start_date"]
        if session_date is None:
            continue
        month = month_values[session_date.month]
        month["enrolled"] += 1
        if registration["assessment_status"] == "passed":
            month["passed"] += 1
        elif registration["assessment_status"] == "failed":
            month["failed"] += 1

    for registration in registrations.values(
        "requested_session__start_date",
        "status",
        "duplicate_review_status",
    ):
        session_date = registration["requested_session__start_date"]
        if session_date is None:
            continue
        month = month_values[session_date.month]
        if (
            registration["status"] == Registration.Status.REJECTED
            or registration["duplicate_review_status"]
            == Registration.DuplicateReviewStatus.EXCLUDED
        ):
            month["rejected"] += 1

    for month_number, historical_values in (
        historical_projection.month_values.items()
    ):
        for key in ("enrolled", "passed", "failed", "rejected"):
            month_values[month_number][key] += historical_values[key]

    chart_peak = max(
        [1]
        + [value["enrolled"] for value in month_values.values()]
        + [
            value["passed"] + value["failed"] + value["rejected"]
            for value in month_values.values()
        ]
    )
    chart_max = max(5, ((chart_peak + 4) // 5) * 5)
    chart_height = 190
    month_names = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    chart_rows = []
    line_points = []
    for index, month_name in enumerate(month_names, start=1):
        values = month_values[index]
        x = 35 + ((index - 1) * 55)
        passed_height = values["passed"] * chart_height / chart_max
        failed_height = values["failed"] * chart_height / chart_max
        rejected_height = values["rejected"] * chart_height / chart_max
        enrolled_y = chart_height - (
            values["enrolled"] * chart_height / chart_max
        )
        chart_rows.append(
            {
                "name": month_name,
                "x": x,
                "passed_y": chart_height - passed_height,
                "passed_height": passed_height,
                "failed_y": chart_height - passed_height - failed_height,
                "failed_height": failed_height,
                "rejected_y": (
                    chart_height
                    - passed_height
                    - failed_height
                    - rejected_height
                ),
                "rejected_height": rejected_height,
                "enrolled_y": enrolled_y,
                "enrolled": values["enrolled"],
                "passed": values["passed"],
                "failed": values["failed"],
                "rejected": values["rejected"],
            }
        )
        line_points.append(f"{x + 14},{enrolled_y:.2f}")
    chart_ticks = [
        {
            "value": round(chart_max * step / 4),
            "y": chart_height - (chart_height * step / 4),
        }
        for step in range(5)
    ]

    operations = [
        {
            "name": "My Courses",
            "items": ["Your assignments", "QR code", "Student names"],
            "url": reverse("my_courses"),
        },
        {
            "name": "Students",
            "items": ["Student records", "Training history"],
            "url": reverse("students"),
        },
    ]
    if administrator:
        operations.extend(
            [
                {
                    "name": "Directory",
                    "items": ["Course master list", "Course status"],
                    "url": reverse("courses_sessions"),
                },
                {
                    "name": "Staff",
                    "items": ["Contact info", "Positions", "Team assignments"],
                    "url": reverse("staff"),
                },
            ]
        )
    if can_view_j35_planner(request.user):
        operations.append(
            {
                "name": "J35 Planner",
                "items": ["Four-week plan", "Instructor allocations"],
                "url": reverse("j35_planner"),
            }
        )
    if can_view_inventory(request.user):
        operations.append(
            {
                "name": "Inventory",
                "items": ["Equipment", "Consumables", "Current inventory"],
                "url": reverse("inventory"),
            }
        )
    if administrator:
        operations.extend(
            [
                {
                    "name": "Reports",
                    "items": ["Course information", "Saved files", "Uploaded files"],
                    "url": reverse("reports"),
                },
                {
                    "name": "Data Quality",
                    "items": ["Review duplicates", "Previous matches", "Errors"],
                    "url": reverse("data_quality"),
                },
            ]
        )

    review_summary = {
        "student_proposals":
            StudentIdentityProposal.objects.exclude(
                proposal_status=(
                    StudentIdentityProposal
                    .ProposalStatus
                    .APPROVED
                )
            ).count(),
        "course_proposals":
            CourseSessionProposal.objects.exclude(
                proposal_status=(
                    CourseSessionProposal
                    .ProposalStatus
                    .APPROVED
                )
            ).count(),
        "alias_reviews":
            EntityAliasProposal.objects.filter(
                proposal_status=(
                    EntityAliasProposal
                    .ProposalStatus
                    .REVIEW_REQUIRED
                )
            ).count(),
        "data_issues":
            DataIssue.objects.filter(
                status__in=[
                    DataIssue.Status.OPEN,
                    DataIssue.Status.REVIEWING,
                ]
            ).count(),
        "evidence_rows": SourceRecord.objects.count(),
    }

    return render(
        request,
        "portal/dashboard.html",
        {
            "totals": totals,
            "courses": Course.objects.filter(active=True).order_by(
                "title_english"
            ),
            "teams": Team.objects.filter(active=True).order_by("name"),
            "instructors": Instructor.objects.filter(active=True).order_by("name_english"),
            "selected_course": selected_course,
            "selected_team": selected_team,
            "selected_instructor": selected_instructor,
            "all_history": all_history,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "chart_rows": chart_rows,
            "chart_ticks": reversed(chart_ticks),
            "chart_height": chart_height,
            "chart_line_points": " ".join(line_points),
            "operations": operations,
            "review_summary": review_summary,
            "historical_totals": historical_projection.totals,
            **_dashboard_j35_context(request),
        },
    )


def _dashboard_j35_context(request):
    from .j35_grid_views import j35_grid_context
    return j35_grid_context(request)


@login_required
def module_page(request, module_key):
    module = MODULES[module_key]

    data_quality_links = []

    if module_key == "data-quality":
        data_quality_links = [
            {
                "label": "Student identity proposals",
                "url": reverse(
                    "admin:portal_studentidentityproposal_changelist"
                ),
            },
            {
                "label": "Course-session proposals",
                "url": reverse(
                    "admin:portal_coursesessionproposal_changelist"
                ),
            },
            {
                "label": "Camp and instructor aliases",
                "url": reverse(
                    "admin:portal_entityaliasproposal_changelist"
                ),
            },
            {
                "label": "Open data issues",
                "url": reverse(
                    "admin:portal_dataissue_changelist"
                ),
            },
            {
                "label": "Historical evidence",
                "url": reverse(
                    "admin:portal_sourcerecord_changelist"
                ),
            },
            {
                "label": "Activity log",
                "url": reverse(
                    "admin:portal_activitylog_changelist"
                ),
            },
        ]

    return render(
        request,
        "portal/module_page.html",
        {
            "module": module,
            "module_key": module_key,
            "data_quality_links": data_quality_links,
        },
    )


@login_required
def student_list(request):
    from django.core.exceptions import PermissionDenied
    from .access import can_view_students
    from .directory_services import filtered_student_rows, OUTCOME_CHOICES
    if not can_view_students(request.user):
        raise PermissionDenied("Student directory access is required.")
    rows = filtered_student_rows(request.GET)
    page_size = request.GET.get("page_size", "50")
    if page_size not in {"25", "50", "100", "all"}:
        page_size = "50"
    paginator = Paginator(rows, max(len(rows), 1) if page_size == "all" else int(page_size))
    page_obj = paginator.get_page(request.GET.get("page"))
    query_parameters = request.GET.copy()
    from .access import can_access_session
    for row in page_obj.object_list:
        row["can_open_course"] = bool(row.get("session") and can_access_session(request.user, row["session"]))
    query_parameters.pop("page", None)
    return render(
        request,
        "portal/student_list.html",
        {
            "page_obj": page_obj,
            "total_matches": paginator.count,
            "unique_students": len({row["student"].pk for row in rows}),
            "current": request.GET,
            "query": request.GET.get("q", ""),
            "identity_status": request.GET.get("identity_status", ""),
            "active_filter": request.GET.get("active", "yes"),
            "sort": request.GET.get("sort", "name"),
            "page_size": page_size,
            "query_string": query_parameters.urlencode(),
            "identity_choices": (("verified", "Verified"), ("review", "Review needed"), ("invalid_id", "Invalid Emirates ID")),
            "outcome_choices": OUTCOME_CHOICES,
            "courses": Course.objects.order_by("title_english"),
        },
    )


@login_required
def student_detail(request, public_id):
    from django.core.exceptions import PermissionDenied
    from .access import can_view_students
    from .directory_services import participation_rows
    if not can_view_students(request.user):
        raise PermissionDenied("Student directory access is required.")
    student = get_object_or_404(
        Student,
        public_id=public_id,
    )
    student.formatted_eid = format_emirates_id(
        student.eid
    )

    training_records = (
        student.training_records
        .select_related(
            "session",
            "session__course",
            "session__camp",
        )
        .order_by(
            "-session__start_date",
            "session__course__title_english",
        )
    )

    return render(
        request,
        "portal/student_detail.html",
        {
            "student": student,
            "training_records": training_records,
            "participations": sorted(
                participation_rows(students=Student.objects.filter(pk=student.pk)),
                key=lambda row: row["date"] or date.min, reverse=True,
            ),
        },
    )


@login_required
@require_POST
def student_print(request):
    from django.core.exceptions import PermissionDenied
    from django.http import QueryDict
    from .access import can_view_students
    from .directory_services import filtered_student_rows
    if not can_view_students(request.user):
        raise PermissionDenied("Student directory access is required.")
    mode = request.POST.get("mode", "selected")
    filters = QueryDict(request.POST.get("filters", ""))
    rows = filtered_student_rows(filters)
    if mode != "all_filtered":
        selected_rows = set(request.POST.getlist("row_keys"))
        legacy_ids = set(request.POST.getlist("shown_ids" if mode == "shown" else "student_ids"))
        rows = [row for row in rows if row["row_key"] in selected_rows or str(row["student"].pk) in legacy_ids]
    # Identity rows are printed once even if selected in more than one course;
    # the established Day 1 print format has no course-participation columns.
    students, seen = [], set()
    for row in rows:
        student = row["student"]
        if student.pk not in seen:
            student.formatted_eid = row["formatted_eid"]
            students.append(student)
            seen.add(student.pk)
    ActivityLog.objects.create(
        actor=request.user,
        action=ActivityLog.Action.PRINT,
        object_type="Student",
        object_id=mode,
        description=(
            f"Prepared {len(students)} students "
            f"for browser printing"
        ),
        details={
            "mode": mode,
            "record_count": len(students),
        },
    )

    return render(
        request,
        "portal/student_print.html",
        {
            "students": students,
            "mode": mode,
            "prepared_at": timezone.localtime(),
            "prepared_by": request.user.get_full_name() or request.user.username,
        },
    )
