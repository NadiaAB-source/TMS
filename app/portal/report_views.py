"""Focused reports with one shared course/calendar frame."""
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Prefetch
from django.shortcuts import render
from django.http import HttpResponse
from django.utils import timezone

from .access import (can_view_reports, can_view_students, can_view_all_courses,
                     can_view_course_directory, can_view_feature, instructor_for_user)
from .course_presentation import lifecycle_label, lifecycle_status
from .course_services import display_course_name
from .directory_services import filter_session_frame, filter_lifecycle, participation_rows, rejected_participation_rows, session_totals, valid_date
from .models import (ActivityLog, Course, CourseSession, CourseInstructor,
                     CourseInstructorInventoryUsage, StampedListArchive, Registration)


REPORT_GROUPS = (
    ("Courses", (("course_all", "All courses"), ("course_completed", "Completed"),
                 ("course_in_progress", "In progress"), ("course_confirmed", "Confirmed"),
                 ("course_draft", "Draft"), ("course_cancelled", "Cancelled"))),
    ("Students", (("student_all", "All outcomes"), ("student_pass", "Passed"),
                  ("student_fail", "Failed"), ("student_rejected", "Rejected"),
                  ("student_hp", "High performance"), ("student_ttt", "TTT"))),
    ("Documents", (("documents_stamped", "Stamped-list status"), ("documents_frontend", "Front-End status"))),
    ("Operations", (("outcomes", "Student outcome totals"), ("instructors", "Course information"),
                    ("inventory", "Equipment use"), ("duplicates", "Duplicates and review"), ("activity", "Activity log"))),
)
REPORT_LABELS = {key: label for _, choices in REPORT_GROUPS for key, label in choices}
LEGACY_REPORTS = {"progress": "course_all", "outcomes": "outcomes", "documents": "documents_all",
                  "instructors": "instructors", "inventory": "inventory", "activity": "activity"}


@login_required
def reports(request):
    if not can_view_reports(request.user):
        raise PermissionDenied("Reporting access is required.")
    selected = request.GET.get("report", "") or LEGACY_REPORTS.get(request.GET.get("view", ""), "course_all")
    if selected not in REPORT_LABELS and selected != "documents_all":
        selected = "course_all"
    base_sessions = filter_session_frame(CourseSession.objects.select_related(
        "course", "camp", "external_upload_confirmed_by"
    ).prefetch_related("instructors", "instructor_assignments", "roster_snapshots__created_by",
                      Prefetch("stamped_list_archives", queryset=StampedListArchive.objects.filter(
                          active=True, document_type=StampedListArchive.DocumentType.STAMPED_LIST
                      ).select_related("uploaded_by"), to_attr="report_stamped_lists")), request.GET)
    sessions_query = base_sessions
    if selected.startswith("course_"):
        sessions_query = filter_lifecycle(sessions_query, selected[7:])
    sessions = list(sessions_query.order_by("-start_date", "-pk"))
    counts = session_totals(sessions)
    all_course_access = can_view_all_courses(request.user) or can_view_course_directory(request.user)
    own_session_ids = set()
    if not all_course_access and can_view_feature(request.user, "own_courses"):
        instructor = instructor_for_user(request.user)
        if instructor:
            own_session_ids = set(CourseInstructor.objects.filter(instructor=instructor, session__in=sessions).values_list("session_id", flat=True))
    for session in sessions:
        session.display_course_name = display_course_name(session)
        session.lifecycle_key = lifecycle_status(session)
        session.lifecycle_label = lifecycle_label(session)
        session.report_can_open = all_course_access or session.pk in own_session_ids
        session.report_totals = counts[session.pk]
        session.latest_roster = next(iter(session.roster_snapshots.all()), None)
        session.current_stamped_list = next(iter(session.report_stamped_lists), None)
        session.frontend_status = ("Upload confirmed" if session.external_upload_confirmed_at else
                                   "Generated — awaiting upload" if session.external_upload_generated_at else "Not generated")
        session.email_sent_count = sum(assignment.notified_at is not None for assignment in session.instructor_assignments.all())
        session.instructor_count = len(session.instructor_assignments.all())

    report_view = "progress"
    student_rows, instructor_rows, inventory_rows, duplicate_rows = [], [], [], []
    if selected.startswith("student_"):
        report_view = "student_details"
        outcome_sessions = base_sessions.exclude(status="draft").exclude(status="cancelled")
        student_rows = participation_rows(sessions=outcome_sessions, include_unlinked=True, valid_only=True)
        choice = selected[8:]
        # Outcomes represent enrolled participations, with rejected applicants
        # kept separately so their exclusion is visible in the rejection report.
        student_rows = [row for row in student_rows if row["enrolled"]]
        if choice == "rejected":
            student_rows = rejected_participation_rows(outcome_sessions)
        elif choice == "all":
            student_rows += rejected_participation_rows(outcome_sessions)
        if choice in {"hp", "ttt"}:
            student_rows = [row for row in student_rows if row["enrolled"] and row[f"is_{choice}"]]
        elif choice != "all":
            student_rows = [row for row in student_rows if row["outcome"] == choice]
        student_rows.sort(key=lambda row: (row["name_english"].casefold(), row["date"] or valid_date("1900-01-01")))
    elif selected.startswith("documents_"):
        report_view = "documents"
        state = request.GET.get("document_state", "")
        if state in {"uploaded", "waiting"}:
            def uploaded(session):
                if selected == "documents_stamped":
                    return bool(session.current_stamped_list)
                if selected == "documents_frontend":
                    return bool(session.external_upload_confirmed_at)
                return bool(session.current_stamped_list and session.external_upload_confirmed_at)
            sessions = [session for session in sessions if uploaded(session) == (state == "uploaded")]
    elif selected == "instructors":
        report_view = "instructors"
        instructor_rows = list(CourseInstructor.objects.filter(session__in=base_sessions).select_related(
            "session__course", "session__camp", "instructor").order_by("-session__start_date", "instructor__name_english"))
        for assignment in instructor_rows:
            assignment.session.display_course_name = display_course_name(assignment.session)
    elif selected == "inventory":
        report_view = "inventory"
        inventory_rows = list(CourseInstructorInventoryUsage.objects.filter(session__in=base_sessions).select_related(
            "session__course", "session__camp", "instructor", "item").order_by("-session__start_date", "instructor__name_english", "item__name"))
        for usage in inventory_rows:
            usage.session.display_course_name = display_course_name(usage.session)
    elif selected == "duplicates":
        from .duplicate_services import build_registration_review_rows
        report_view = "duplicates"
        registrations = Registration.objects.filter(requested_session__in=base_sessions).select_related(
            "student", "requested_session__course", "requested_session__camp", "duplicate_reviewed_by")
        duplicate_rows = [r for r in build_registration_review_rows(list(registrations)) if r["review_required"]]
        review_state = request.GET.get("review_state", "all")
        if review_state in {"required", "allowed", "excluded"}:
            duplicate_rows = [r for r in duplicate_rows if r["review_state"] == review_state]
    elif selected in {"outcomes", "activity"}:
        report_view = selected

    activity_rows = ActivityLog.objects.select_related("actor").order_by("-created_at", "-pk")
    activity_date = valid_date(request.GET.get("activity_date"))
    if activity_date:
        activity_rows = activity_rows.filter(created_at__date=activity_date)
    for key, lookup in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
        parsed = valid_date(request.GET.get(key))
        if parsed:
            activity_rows = activity_rows.filter(**{lookup: parsed})
    activity_user = request.GET.get("activity_user", "")
    if activity_user.isdigit():
        activity_rows = activity_rows.filter(actor_id=int(activity_user))
    activity_action = request.GET.get("activity_action", "")
    if activity_action in ActivityLog.Action.values:
        activity_rows = activity_rows.filter(action=activity_action)
    if str(request.GET.get("course", "")).isdigit():
        session_ids = [str(value) for value in base_sessions.values_list("public_id", flat=True)]
        activity_rows = activity_rows.filter(Q(object_type="CourseSession", object_id__in=session_ids) | Q(details__session__in=session_ids))

    frame = {key: request.GET.get(key, "") for key in ("course", "date_from", "date_to")}
    groups = [{"label": label, "choices": [{"key": key, "label": title,
               "url": "?" + urlencode({**frame, "report": key})} for key, title in choices]} for label, choices in REPORT_GROUPS]
    report_title = REPORT_LABELS.get(selected, "Document status")
    if request.GET.get("view") == "progress":
        report_title = "Course progress"
    elif request.GET.get("view") == "outcomes":
        report_title = "Student outcomes"
    context = {
        "selected_report": selected, "report_view": report_view, "report_title": report_title,
        "student_history_access": can_view_students(request.user),
        "report_groups": groups, "courses": Course.objects.order_by("title_english"), "current": request.GET,
        "sessions": sessions, "student_rows": student_rows, "instructor_rows": instructor_rows,
        "inventory_rows": inventory_rows, "duplicate_rows": duplicate_rows,
        "recent_activity": activity_rows if request.GET.get("export") == "xlsx" else activity_rows[:250],
        "activity_users": get_user_model().objects.filter(tms_activity__isnull=False).distinct().order_by("username"),
        "activity_actions": ActivityLog.Action.choices,
        "result_count": len(student_rows) if report_view == "student_details" else len(sessions),
    }
    if request.GET.get("export") == "xlsx":
        from .report_export import workbook_bytes, report_table
        headers, values = report_table(context)
        scope = [("Report", report_title)]
        course = Course.objects.filter(pk=int(frame["course"])).first() if frame["course"].isdigit() else None
        scope += [("Course", str(course) if course else "All courses"),
                  ("Date from", frame["date_from"]), ("Date to", frame["date_to"])]
        for key, label in (("document_state", "Upload status"), ("review_state", "Duplicate review"),
                           ("activity_date", "Activity date"), ("activity_user", "Activity user ID"), ("activity_action", "Activity action")):
            if request.GET.get(key):
                scope.append((label, request.GET[key]))
        response = HttpResponse(workbook_bytes(report_title, headers, values, scope, tz=timezone.get_current_timezone()),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="IQARUS_{selected}_{timezone.localdate().isoformat()}.xlsx"'
        response["Cache-Control"] = "private, no-store"
        return response
    return render(request, "portal/reports.html", context)
