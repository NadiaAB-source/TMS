
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import (
    Camp,
    Course,
    CourseSession,
    CourseSessionProposal,
    Instructor,
)
from .course_services import display_course_name, display_reference
from .access import can_view_course_directory
from .directory_services import LIFECYCLE_CHOICES, filter_lifecycle, filter_session_frame, session_totals
from .course_presentation import lifecycle_status, lifecycle_label


def _text_lookups(model, prefix="", depth=0, visited=None):
    visited = set(visited or set())

    if model in visited:
        return []

    visited.add(model)
    lookups = []

    for field in model._meta.get_fields():
        if (
            getattr(field, "concrete", False)
            and isinstance(field, (models.CharField, models.TextField))
        ):
            lookups.append(f"{prefix}{field.name}")

        elif (
            depth < 1
            and getattr(field, "concrete", False)
            and getattr(field, "many_to_one", False)
            and getattr(field, "related_model", None)
        ):
            lookups.extend(
                _text_lookups(
                    field.related_model,
                    prefix=f"{prefix}{field.name}__",
                    depth=depth + 1,
                    visited=visited.copy(),
                )
            )

    return lookups


def _session_row(session):
    instructors = list(session.instructors.all())

    return {
        "public_id": session.public_id,
        "course": display_course_name(session),
        "reference_code": session.reference_code or "Not assigned",
        "display_reference": display_reference(session),
        "start_date": session.start_date,
        "end_date": session.end_date,
        "camp": str(session.camp) if session.camp else "Not assigned",
        "status": lifecycle_label(session),
        "status_key": lifecycle_status(session),
        "instructors": instructors,
        "registration_published": session.registration_published,
    }


@login_required
def course_session_list(request):
    if not can_view_course_directory(request.user):
        raise PermissionDenied("The master course directory is limited to administrator roles.")
    queryset = (
        CourseSession.objects
        .select_related("course", "camp")
        .prefetch_related("instructors")
    )

    search_text = request.GET.get("q", "").strip()
    course_id = request.GET.get("course", "").strip()
    camp_id = request.GET.get("camp", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if search_text:
        search_query = Q()

        for lookup in _text_lookups(CourseSession):
            search_query |= Q(**{f"{lookup}__icontains": search_text})

        queryset = queryset.filter(search_query)

    if course_id.isdigit():
        queryset = queryset.filter(course_id=course_id)

    if camp_id.isdigit():
        queryset = queryset.filter(camp_id=camp_id)

    queryset = filter_lifecycle(queryset, status)
    queryset = filter_session_frame(queryset, request.GET)
    instructor_id = request.GET.get("instructor", "").strip()
    if instructor_id.isdigit():
        queryset = queryset.filter(instructors__pk=int(instructor_id))
    registration = request.GET.get("registration", "")
    if registration in {"open", "closed"}:
        queryset = queryset.filter(registration_published=registration == "open")
    queryset = queryset.distinct().order_by("-start_date", "-id")
    sessions = list(queryset)
    totals = session_totals(sessions)
    rows = []
    numeric_filters = {key: request.GET.get(key, "").strip() for key in ("enrolled", "passed", "failed", "rejected", "hp", "ttt")}
    for session in sessions:
        values = totals[session.pk]
        if any(value.isdigit() and values[key] != int(value) for key, value in numeric_filters.items()):
            continue
        row = _session_row(session)
        row.update(values)
        rows.append(row)
    filtered_count = len(rows)
    paginator = Paginator(rows, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = page_obj.object_list
    for index, row in enumerate(rows, page_obj.start_index()):
        row["row_number"] = index
    status_choices = LIFECYCLE_CHOICES

    query_copy = request.GET.copy()
    query_copy.pop("page", None)

    context = {
        "active_module": "courses_sessions",
        "page_title": "Courses & Sessions",
        "header_title": "Courses & Sessions",
        "workspace_title": "Courses & Sessions",
        "rows": rows,
        "row_height": max(30, max((len(row["instructors"]) for row in rows), default=0) * 26 + 4),
        "page_obj": page_obj,
        "total_courses": Course.objects.count(),
        "total_sessions": CourseSession.objects.count(),
        "filtered_count": filtered_count,
        "review_count": CourseSessionProposal.objects.exclude(
            proposal_status="approved"
        ).count(),
        "published_count": CourseSession.objects.filter(
            registration_published=True
        ).count(),
        "courses": Course.objects.all().order_by("pk"),
        "camps": Camp.objects.all().order_by("pk"),
        "instructors": Instructor.objects.filter(active=True).order_by("name_english"),
        "numeric_filters": [{"key": key, "label": key.upper() if key in {"hp", "ttt"} else key.title(), "value": value} for key, value in numeric_filters.items()],
        "status_choices": status_choices,
        "query_without_page": query_copy.urlencode(),
        "current": {
            "q": search_text,
            "course": course_id,
            "camp": camp_id,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "instructor": instructor_id,
            "registration": registration,
        },
    }

    return render(
        request,
        "portal/course_session_list.html",
        context,
    )


@login_required
def course_session_detail(request, public_id):
    if not can_view_course_directory(request.user):
        raise PermissionDenied("The master course directory is limited to administrator roles.")
    session = get_object_or_404(
        CourseSession.objects
        .select_related("course", "camp")
        .prefetch_related("instructors"),
        public_id=public_id,
    )
    session.display_reference = display_reference(session)
    session.display_course_name = display_course_name(session)

    proposals = CourseSessionProposal.objects.filter(
        approved_session=session
    ).order_by("created_at")

    status_method = getattr(session, "get_status_display", None)
    status = status_method() if callable(status_method) else session.status

    context = {
        "active_module": "courses_sessions",
        "page_title": "Courses & Sessions",
        "header_title": "Courses & Sessions",
        "workspace_title": "Courses & Sessions",
        "session": session,
        "status": status,
        "instructors": session.instructors.all(),
        "proposals": proposals,
    }

    return render(
        request,
        "portal/course_session_detail.html",
        context,
    )
