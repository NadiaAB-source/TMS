"""Shared dashboard J35 spreadsheet context and protected AJAX endpoint."""

from collections import defaultdict
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.db.models import Q
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import can_manage_j35_planner, can_view_j35_planner
from .j35_grid_services import clear_grid_cell, save_grid_cell
from .models import Camp, Course, CourseInstructor, CourseSession, Instructor, InstructorAllocation


def _start(request):
    try:
        value = date.fromisoformat(request.GET.get("j35_start") or request.POST.get("j35_start") or "")
        value + timedelta(days=365)
        value - timedelta(days=365)
        return value
    except (ValueError, TypeError, OverflowError):
        today = timezone.localdate()
        return today - timedelta(days=today.weekday())


def _contrast(color):
    values = [int(color[offset:offset + 2], 16) / 255 for offset in (1, 3, 5)]
    values = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in values]
    luminance = .2126 * values[0] + .7152 * values[1] + .0722 * values[2]
    return "#111111" if luminance > .179 else "#ffffff"


def _cell_data(allocation):
    try:
        cell = allocation.grid_cell
    except InstructorAllocation.grid_cell.RelatedObjectDoesNotExist:
        cell = None
    course = cell.course if cell and cell.course_id else (
        allocation.session.course if allocation.session_id else None
    )
    color = cell.color if cell else "#ffffff"
    return {
        "id": allocation.pk,
        "revision": cell.revision if cell else 0,
        "activity": (
            allocation.camp.name if cell is None and allocation.camp_id
            and allocation.allocation_kind == InstructorAllocation.AllocationKind.COURSE
            else allocation.activity
        ),
        "camp": allocation.camp_id or "",
        "course": course.pk if course else "",
        "course_label": course.title_english if course else "",
        "kind": allocation.allocation_kind,
        "start_date": allocation.start_date.isoformat(),
        "end_date": allocation.end_date.isoformat(),
        "status": "confirmed" if allocation.status == InstructorAllocation.Status.PUBLISHED else "draft",
        "status_label": "Confirmed" if allocation.status == InstructorAllocation.Status.PUBLISHED else "Draft",
        "color": color,
        "ink": _contrast(color),
        "notes": allocation.notes,
        "override_reason": allocation.override_reason,
        "session": allocation.session_id or "",
        "instructor": allocation.instructor_id,
    }


def j35_grid_context(request):
    """Merge this dictionary into dashboard context; dates are independent of KPIs."""
    if not can_view_j35_planner(request.user):
        return {"j35_grid_visible": False}
    start = _start(request)
    end = start + timedelta(days=13)
    days = [start + timedelta(days=offset) for offset in range(14)]
    instructors = list(
        Instructor.objects.filter(active=True).select_related("team")
        .prefetch_related("roles").order_by("team__name", "name_english", "pk")
    )
    allocations = InstructorAllocation.objects.filter(
        instructor__in=instructors, start_date__lte=end, end_date__gte=start,
        status__in=[InstructorAllocation.Status.DRAFT, InstructorAllocation.Status.PUBLISHED],
    ).select_related("grid_cell__course", "session__course", "camp").order_by("start_date", "pk")
    by_day = defaultdict(list)
    for allocation in allocations:
        cell = _cell_data(allocation)
        for day in days:
            if allocation.start_date <= day <= allocation.end_date:
                by_day[(allocation.instructor_id, day)].append(cell)
    # Courses created through the course workspace also occupy the instructor's
    # calendar. Show them without manufacturing or rewriting historical J35 rows.
    bookings = CourseInstructor.objects.filter(
        instructor__in=instructors, session__start_date__lte=end,
    ).filter(
        Q(session__end_date__gte=start)
        | Q(session__end_date__isnull=True, session__start_date__gte=start)
    ).exclude(
        session__status__in=[CourseSession.Status.DRAFT, CourseSession.Status.CANCELLED]
    ).select_related("session__course", "session__camp")
    for booking in bookings:
        session = booking.session
        for day in days:
            assigned = by_day[(booking.instructor_id, day)]
            if (
                session.start_date <= day <= (session.end_date or session.start_date)
                and not any(cell["session"] == session.pk for cell in assigned)
            ):
                assigned.append({
                    "id": f"course-{booking.pk}", "read_only": True,
                    "activity": session.camp.name if session.camp_id else session.course.title_english,
                    "course_label": session.course.title_english,
                    "course_url": reverse("instructor_course_workspace", kwargs={"public_id": session.public_id}),
                    "status": "confirmed", "status_label": "Confirmed course",
                    "session": session.pk, "color": "#eef1f3", "ink": "#111111",
                })
    rows = []
    for number, instructor in enumerate(instructors, 1):
        rows.append({
            "number": number,
            "instructor": instructor,
            "roles": ", ".join(role.name for role in instructor.roles.all() if role.active),
            "days": [{"date": day, "cells": by_day[(instructor.pk, day)]} for day in days],
        })
    def week_url(delta):
        query = request.GET.copy()
        query["j35_start"] = (start + timedelta(days=delta)).isoformat()
        return "?" + query.urlencode() + "#j35-grid"
    preserved = [(key, val) for key in request.GET for val in request.GET.getlist(key) if key != "j35_start"]
    return {
        "j35_grid_visible": True,
        "j35_grid_editable": can_manage_j35_planner(request.user),
        "j35_grid_start": start,
        "j35_grid_end": end,
        "j35_grid_days": days,
        "j35_grid_rows": rows,
        "j35_grid_row_height": max(1, max((len(cells) for cells in by_day.values()), default=1)) * 32 + 1,
        "j35_grid_previous": week_url(-7),
        "j35_grid_next": week_url(7),
        "j35_grid_preserved": preserved,
        "j35_grid_camps": Camp.objects.filter(active=True).order_by("name"),
        "j35_grid_courses": Course.objects.filter(active=True).order_by("title_english"),
        "j35_grid_sessions": CourseSession.objects.filter(start_date__lte=end)
        .filter(start_date__gte=start - timedelta(days=365))
        .exclude(status=CourseSession.Status.CANCELLED)
        .select_related("course", "camp").order_by("-start_date", "pk")[:300],
    }


@login_required
def j35_grid(request):
    if not can_view_j35_planner(request.user):
        raise PermissionDenied("You do not have permission to view J35.")
    if request.method == "POST" and request.POST.get("action"):
        if not can_manage_j35_planner(request.user):
            raise PermissionDenied("You do not have permission to edit J35.")
        # Existing automation and camp-request workflows keep their original
        # validation and audit path; the dashboard uses the new sheet editor.
        from .planner_views import j35_planner
        return j35_planner(request)
    if request.method != "GET":
        # Old planner POST payloads are intentionally not interpreted by the new editor.
        return JsonResponse({"ok": False, "error": "Use the J35 grid to edit this plan."}, status=405)
    return render(request, "portal/j35_grid.html", j35_grid_context(request))


@login_required
@require_POST
def j35_grid_save(request):
    if not can_manage_j35_planner(request.user):
        return JsonResponse({"ok": False, "error": "You do not have permission to edit J35."}, status=403)
    try:
        operation = clear_grid_cell if request.POST.get("action") == "clear" else save_grid_cell
        allocation = operation(user=request.user, data=request.POST, request=request)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=400)
    context = j35_grid_context(request)
    return JsonResponse({
        "ok": True,
        "allocation_id": allocation.pk,
        "instructor_id": allocation.instructor_id,
        "message": (
            "Draft cell cleared." if allocation.status == InstructorAllocation.Status.CANCELLED
            else "J35 assignment saved." if allocation.status == InstructorAllocation.Status.DRAFT
            else "J35 assignment confirmed."
        ),
        "rows_html": render_to_string("portal/_j35_grid_rows.html", context, request=request),
        "row_height": context["j35_grid_row_height"],
    })
