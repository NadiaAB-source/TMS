
from io import BytesIO
import os
import re

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

import qrcode
from qrcode.image.svg import SvgPathImage

from .models import (
    ActivityLog,
    CourseInstructor,
    CourseSession,
    Instructor,
    Registration,
    Student,
)
from .access import require_session_access
from .course_services import display_course_name, display_reference


def _digits(value):
    return "".join(
        character
        for character in str(value or "")
        if character.isdigit()
    )


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",", 1)[0].strip() if forwarded else None) or (
        request.META.get("REMOTE_ADDR") or None
    )


def _session_date_text(session):
    if session.start_date and session.end_date:
        if session.start_date == session.end_date:
            return session.start_date.strftime("%d %B %Y")

        return (
            session.start_date.strftime("%d %B %Y")
            + " – "
            + session.end_date.strftime("%d %B %Y")
        )

    if session.start_date:
        return session.start_date.strftime("%d %B %Y")

    return "Date to be confirmed"



def _public_registration_url(request, session):
    registration_path = reverse(
        "course_registration",
        kwargs={
            "token": session.public_registration_token,
        },
    )

    public_base_url = os.environ.get(
        "IQARUS_PUBLIC_BASE_URL",
        "",
    ).strip().rstrip("/")

    if public_base_url:
        return public_base_url + registration_path

    return request.build_absolute_uri(registration_path)

def _matching_student(eid_digits):
    matches = []

    for student in Student.objects.exclude(
        eid__isnull=True
    ).exclude(eid="").only("id", "eid"):
        if _digits(student.eid) == eid_digits:
            matches.append(student)

            if len(matches) > 1:
                return None

    return matches[0] if len(matches) == 1 else None


def _accessible_session(request, public_id):
    queryset = (
        CourseSession.objects
        .select_related("course", "camp")
        .prefetch_related("instructors")
    )

    session = get_object_or_404(
        queryset,
        public_id=public_id,
    )

    require_session_access(request.user, session)
    return session


class CourseRegistrationForm(forms.Form):
    name_arabic = forms.CharField(
        label="Arabic name / الاسم باللغة العربية",
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "dir": "rtl",
                "autocomplete": "name",
                "placeholder": "الاسم الكامل",
            }
        ),
    )

    name_english = forms.CharField(
        label="English name / الاسم باللغة الإنجليزية",
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "name",
                "placeholder": "FULL NAME",
                "class": "uppercase-input",
            }
        ),
    )

    emirates_id = forms.CharField(
        label="Emirates ID / رقم الهوية الإماراتية",
        max_length=30,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "784-XXXX-XXXXXXX-X",
            }
        ),
    )

    email = forms.EmailField(
        label="Email / البريد الإلكتروني",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "placeholder": "name@example.com",
            }
        ),
    )

    unit = forms.CharField(
        label="Unit / الوحدة",
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "organization",
                "placeholder": "Unit name",
            }
        ),
    )

    def clean_name_arabic(self):
        return self.cleaned_data["name_arabic"].strip()

    def clean_name_english(self):
        return self.cleaned_data["name_english"].strip().upper()

    def clean_emirates_id(self):
        digits = _digits(self.cleaned_data["emirates_id"])

        if len(digits) != 15:
            raise forms.ValidationError(
                "Enter all 15 digits of the Emirates ID."
            )

        if not digits.startswith("784"):
            raise forms.ValidationError(
                "The Emirates ID must begin with 784."
            )

        birth_year_text = digits[3:7]
        current_year = timezone.localdate().year

        try:
            birth_year = int(birth_year_text)
        except (TypeError, ValueError):
            raise forms.ValidationError(
                "The second section must be the four-digit year of birth."
            )

        if birth_year < 1900 or birth_year > current_year:
            raise forms.ValidationError(
                "The second section must be a valid year of birth "
                f"between 1900 and {current_year}."
            )

        return digits

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_unit(self):
        return self.cleaned_data["unit"].strip()


@require_http_methods(["GET", "POST"])
def course_registration(request, token):
    session = get_object_or_404(
        CourseSession.objects
        .select_related("course", "camp")
        .prefetch_related("instructors"),
        public_registration_token=token,
    )
    session.display_reference = display_reference(session)
    session.display_course_name = display_course_name(session)

    registration_open = (
        session.registration_published
        and session.status == "registration_open"
    )

    success = False
    already_registered = False

    if request.method == "POST" and registration_open:
        form = CourseRegistrationForm(request.POST)

        if form.is_valid():
            eid_digits = form.cleaned_data["emirates_id"]

            with transaction.atomic():
                CourseSession.objects.select_for_update().get(
                    pk=session.pk
                )

                existing_registration = Registration.objects.filter(
                    requested_session=session,
                    eid_normalized=eid_digits,
                ).first()

                if existing_registration:
                    success = True
                    already_registered = True
                else:
                    student = _matching_student(eid_digits)

                    Registration.objects.create(
                        requested_session=session,
                        student=student,
                        submitted_name_english=(
                            form.cleaned_data["name_english"]
                        ),
                        submitted_name_arabic=(
                            form.cleaned_data["name_arabic"]
                        ),
                        eid_raw=eid_digits,
                        eid_normalized=eid_digits,
                        email_raw=form.cleaned_data["email"],
                        phone_raw="",
                        submitted_unit=form.cleaned_data["unit"],
                        training_date_raw=_session_date_text(session),
                        submitted_at=timezone.now(),
                        status="pending",
                        source_file=None,
                        source_sheet="Course QR registration",
                        source_row=None,
                        raw_payload={
                            "submission_type": "course_qr",
                            "name_arabic": (
                                form.cleaned_data["name_arabic"]
                            ),
                            "name_english": (
                                form.cleaned_data["name_english"]
                            ),
                            "emirates_id": eid_digits,
                            "email": form.cleaned_data["email"],
                            "unit": form.cleaned_data["unit"],
                            "course_session": str(session.public_id),
                        },
                        review_notes="",
                    )

                    success = True

            if success:
                form = CourseRegistrationForm()
    else:
        form = CourseRegistrationForm()

    context = {
        "session": session,
        "form": form,
        "registration_open": registration_open,
        "success": success,
        "already_registered": already_registered,
        "date_text": _session_date_text(session),
    }

    return render(
        request,
        "portal/course_registration.html",
        context,
    )


@login_required
@require_http_methods(["GET", "POST"])
def registration_qr(request, public_id):
    session = _accessible_session(request, public_id)
    session.display_reference = display_reference(session)
    session.display_course_name = display_course_name(session)

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "open":
            if session.status in {"completed", "cancelled"}:
                messages.error(
                    request,
                    "A completed or cancelled course cannot be opened.",
                )
            else:
                session.registration_published = True
                session.status = "registration_open"
                session.save(
                    update_fields=[
                        "registration_published",
                        "status",
                        "updated_at",
                    ]
                )
                ActivityLog.objects.create(
                    actor=request.user,
                    action=ActivityLog.Action.UPDATE,
                    object_type="CourseSession",
                    object_id=str(session.public_id),
                    description="Student registration opened.",
                    details={"reference_code": session.reference_code},
                    ip_address=_client_ip(request),
                )

                messages.success(
                    request,
                    "Student registration is now open.",
                )

        elif action == "close":
            session.registration_published = False

            if session.status == "registration_open":
                session.status = "registration_closed"

            session.save(
                update_fields=[
                    "registration_published",
                    "status",
                    "updated_at",
                ]
            )
            ActivityLog.objects.create(
                actor=request.user,
                action=ActivityLog.Action.UPDATE,
                object_type="CourseSession",
                object_id=str(session.public_id),
                description="Student registration closed.",
                details={"reference_code": session.reference_code},
                ip_address=_client_ip(request),
            )

            messages.success(
                request,
                "Student registration is now closed.",
            )

        return redirect(
            "registration_qr",
            public_id=session.public_id,
        )

    registration_url = _public_registration_url(request, session)

    context = {
        "active_module": "my_courses",
        "session": session,
        "registration_url": registration_url,
        "date_text": _session_date_text(session),
        "registration_count": Registration.objects.filter(
            requested_session=session
        ).count(),
    }

    return render(
        request,
        "portal/registration_qr.html",
        context,
    )


@login_required
def registration_qr_image(request, public_id):
    session = _accessible_session(request, public_id)

    registration_url = _public_registration_url(request, session)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )

    qr.add_data(registration_url)
    qr.make(fit=True)

    image = qr.make_image(image_factory=SvgPathImage)
    output = BytesIO()
    image.save(output)

    return HttpResponse(
        output.getvalue(),
        content_type="image/svg+xml",
    )
