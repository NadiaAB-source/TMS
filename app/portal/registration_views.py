
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .identity import format_emirates_id, masked_emirates_id
from .models import CourseSession, Registration


def _masked_eid(value):
    return masked_emirates_id(value)


def _status_display(registration):
    method = getattr(registration, "get_status_display", None)

    if callable(method):
        return method()

    return registration.status.replace("_", " ").title()


def _registration_row(registration):
    submitted_name = (
        registration.submitted_name_english
        or registration.submitted_name_arabic
        or (
            str(registration.student)
            if registration.student
            else "Name not supplied"
        )
    )

    return {
        "public_id": registration.public_id,
        "submitted_name": submitted_name,
        "arabic_name": registration.submitted_name_arabic,
        "eid": _masked_eid(
            registration.eid_normalized or registration.eid_raw
        ),
        "email": registration.email_raw or "Not supplied",
        "phone": registration.phone_raw or "Not supplied",
        "training_date": (
            registration.training_date_raw or "Not supplied"
        ),
        "submitted_at": registration.submitted_at,
        "status": _status_display(registration),
        "status_code": registration.status,
        "student_linked": registration.student_id is not None,
        "requested_session": (
            str(registration.requested_session)
            if registration.requested_session
            else "Not linked"
        ),
    }


@login_required
def registration_list(request):
    queryset = (
        Registration.objects
        .select_related(
            "student",
            "requested_session",
            "source_file",
        )
    )

    search_text = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    identity = request.GET.get("identity", "").strip()
    session_id = request.GET.get("session", "").strip()
    submitted_from = request.GET.get("submitted_from", "").strip()
    submitted_to = request.GET.get("submitted_to", "").strip()

    if search_text:
        queryset = queryset.filter(
            Q(submitted_name_english__icontains=search_text)
            | Q(submitted_name_arabic__icontains=search_text)
            | Q(eid_raw__icontains=search_text)
            | Q(eid_normalized__icontains=search_text)
            | Q(email_raw__icontains=search_text)
            | Q(phone_raw__icontains=search_text)
            | Q(training_date_raw__icontains=search_text)
        )

    if status:
        queryset = queryset.filter(status=status)

    if identity == "linked":
        queryset = queryset.filter(student__isnull=False)
    elif identity == "unlinked":
        queryset = queryset.filter(student__isnull=True)

    if session_id:
        queryset = queryset.filter(requested_session_id=session_id)

    if submitted_from:
        queryset = queryset.filter(
            submitted_at__date__gte=submitted_from
        )

    if submitted_to:
        queryset = queryset.filter(
            submitted_at__date__lte=submitted_to
        )

    queryset = queryset.order_by("-submitted_at", "-id")

    try:
        per_page = int(request.GET.get("per_page", 50))
    except (TypeError, ValueError):
        per_page = 50

    if per_page not in {25, 50, 100}:
        per_page = 50

    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = [
        _registration_row(registration)
        for registration in page_obj.object_list
    ]

    status_field = Registration._meta.get_field("status")
    status_choices = [
        (str(value), str(label))
        for value, label in status_field.choices
    ]

    query_copy = request.GET.copy()
    query_copy.pop("page", None)

    context = {
        "active_module": "registrations",
        "page_title": "Registrations",
        "header_title": "Registrations",
        "workspace_title": "Registrations",
        "rows": rows,
        "page_obj": page_obj,
        "total_registrations": Registration.objects.count(),
        "pending_count": Registration.objects.filter(
            status="pending"
        ).count(),
        "review_count": Registration.objects.filter(
            status="needs_review"
        ).count(),
        "linked_count": Registration.objects.filter(
            student__isnull=False
        ).count(),
        "filtered_count": queryset.count(),
        "status_choices": status_choices,
        "sessions": CourseSession.objects.select_related(
            "course",
            "camp",
        ).order_by("-start_date", "-id"),
        "per_page": per_page,
        "query_without_page": query_copy.urlencode(),
        "current": {
            "q": search_text,
            "status": status,
            "identity": identity,
            "session": session_id,
            "submitted_from": submitted_from,
            "submitted_to": submitted_to,
        },
    }

    return render(
        request,
        "portal/registration_list.html",
        context,
    )


@login_required
def registration_detail(request, public_id):
    registration = get_object_or_404(
        Registration.objects.select_related(
            "student",
            "requested_session",
            "source_file",
        ),
        public_id=public_id,
    )

    context = {
        "active_module": "registrations",
        "page_title": "Registration Details",
        "header_title": "Registration Details",
        "workspace_title": "Registration Details",
        "registration": registration,
        "eid_display": format_emirates_id(
            registration.eid_normalized or registration.eid_raw,
            fallback="Not supplied",
        ),
        "status_display": _status_display(registration),
    }

    return render(
        request,
        "portal/registration_detail.html",
        context,
    )

