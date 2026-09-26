from collections import Counter, defaultdict
import hashlib
import json

from django.db.models import Count

from .identity import format_emirates_id
from .models import Student, TrainingRecord


def normal_text(value):
    return " ".join(str(value or "").casefold().split())


def normal_eid(value):
    return "".join(
        character
        for character in str(value or "")
        if character.isdigit()
    )


def build_registration_review_rows(registrations):
    """Return deterministic duplicate/history findings for registration rows.

    Findings are advisory. They never merge or delete students; a human records
    an allow/exclude decision on the registration before roster selection.
    """
    registrations = list(registrations)
    students = list(
        Student.objects.only(
            "id",
            "public_id",
            "eid",
            "name_english",
            "name_arabic",
            "email",
            "created_at",
        ).order_by("id")
    )

    students_by_eid = defaultdict(list)
    students_by_english = defaultdict(list)
    students_by_arabic = defaultdict(list)
    students_by_email = defaultdict(list)
    for student in students:
        eid = normal_eid(student.eid)
        english_name = normal_text(student.name_english)
        arabic_name = normal_text(student.name_arabic)
        email = normal_text(student.email)
        if eid:
            students_by_eid[eid].append(student)
        if english_name:
            students_by_english[english_name].append(student)
        if arabic_name:
            students_by_arabic[arabic_name].append(student)
        if email:
            students_by_email[email].append(student)

    current_eids = Counter(
        (
            registration.requested_session_id,
            normal_eid(registration.eid_normalized or registration.eid_raw),
        )
        for registration in registrations
        if normal_eid(registration.eid_normalized or registration.eid_raw)
    )
    current_english_names = Counter(
        (
            registration.requested_session_id,
            normal_text(registration.submitted_name_english),
        )
        for registration in registrations
        if normal_text(registration.submitted_name_english)
    )
    current_emails = Counter(
        (
            registration.requested_session_id,
            normal_text(registration.email_raw),
        )
        for registration in registrations
        if normal_text(registration.email_raw)
    )

    candidates_by_registration = {}
    candidate_ids = set()
    for registration in registrations:
        eid_matches = students_by_eid.get(
            normal_eid(registration.eid_normalized or registration.eid_raw),
            [],
        )
        english_matches = students_by_english.get(
            normal_text(registration.submitted_name_english),
            [],
        )
        arabic_matches = students_by_arabic.get(
            normal_text(registration.submitted_name_arabic),
            [],
        )
        email_matches = students_by_email.get(
            normal_text(registration.email_raw),
            [],
        )
        candidates = {
            student.id: student
            for student in (
                list(eid_matches)
                + list(english_matches)
                + list(arabic_matches)
                + list(email_matches)
            )
        }
        candidates_by_registration[registration.id] = {
            "eid": list(eid_matches),
            "english": list(english_matches),
            "arabic": list(arabic_matches),
            "email": list(email_matches),
            "all": list(candidates.values()),
        }
        candidate_ids.update(candidates)

    history_totals = Counter()
    history_by_course = Counter()
    history_by_session = Counter()
    if candidate_ids:
        history_rows = (
            TrainingRecord.objects.filter(student_id__in=candidate_ids)
            .values("student_id", "session_id", "session__course_id")
            .annotate(total=Count("id"))
        )
        for history in history_rows:
            student_id = history["student_id"]
            count = history["total"]
            history_totals[student_id] += count
            history_by_session[(student_id, history["session_id"])] += count
            history_by_course[
                (student_id, history["session__course_id"])
            ] += count

    rows = []
    for serial, registration in enumerate(registrations, start=1):
        matches = candidates_by_registration[registration.id]
        exact_eid_matches = matches["eid"]
        flags = []

        def add_flag(code, title, detail, tone="warning"):
            if code not in {flag["code"] for flag in flags}:
                flags.append(
                    {
                        "code": code,
                        "title": title,
                        "detail": detail,
                        "tone": tone,
                    }
                )

        session_id = registration.requested_session_id
        course_id = (
            registration.requested_session.course_id
            if registration.requested_session_id
            else None
        )
        eid = normal_eid(registration.eid_normalized or registration.eid_raw)
        english_name = normal_text(registration.submitted_name_english)
        email = normal_text(registration.email_raw)

        if eid and current_eids[(session_id, eid)] > 1:
            add_flag(
                "course_eid_duplicate",
                "Duplicate QR registration",
                "This Emirates ID appears more than once in this course.",
                "danger",
            )
        if english_name and current_english_names[(session_id, english_name)] > 1:
            add_flag(
                "course_name_duplicate",
                "Repeated course name",
                "This English name appears more than once in this course registration list.",
                "danger",
            )
        if email and current_emails[(session_id, email)] > 1:
            add_flag(
                "course_email_duplicate",
                "Repeated course email",
                "This email appears more than once in this course registration list.",
            )

        if len(exact_eid_matches) > 1:
            add_flag(
                "multiple_master_ids",
                "Conflicting master records",
                "More than one master student record uses this Emirates ID.",
                "danger",
            )
        elif len(exact_eid_matches) == 1:
            master = exact_eid_matches[0]
            created_from_this_registration = (
                registration.student_id == master.id
                and master.created_at >= registration.created_at
            )
            if not created_from_this_registration:
                add_flag(
                    "master_id_match",
                    "Existing master student",
                    f"Emirates ID matches {master.display_name} in the master student log.",
                )
                if (
                    normal_text(master.name_english)
                    and english_name
                    and normal_text(master.name_english) != english_name
                ):
                    add_flag(
                        "identity_name_conflict",
                        "Identity details conflict",
                        "The Emirates ID matches a master student, but the English name is different.",
                        "danger",
                    )

        exact_ids = {student.id for student in exact_eid_matches}
        name_only_matches = {
            student.id: student
            for student in matches["english"] + matches["arabic"]
            if student.id not in exact_ids
        }
        if name_only_matches:
            add_flag(
                "master_name_match",
                "Possible name duplicate",
                f"The submitted name matches {len(name_only_matches)} other master student record(s) with a different ID.",
            )
        email_only_matches = {
            student.id: student
            for student in matches["email"]
            if student.id not in exact_ids
        }
        if email_only_matches:
            add_flag(
                "master_email_match",
                "Possible email duplicate",
                f"The email matches {len(email_only_matches)} other master student record(s).",
            )

        previous_records = 0
        previous_same_course = 0
        for student in matches["all"]:
            previous_records += (
                history_totals[student.id]
                - history_by_session[(student.id, session_id)]
            )
            previous_same_course += (
                history_by_course[(student.id, course_id)]
                - history_by_session[(student.id, session_id)]
            )
        if previous_same_course:
            add_flag(
                "previous_same_course",
                "Previously attended this course",
                f"Master history contains {previous_same_course} earlier record(s) for this same course.",
                "danger",
            )
        elif previous_records:
            add_flag(
                "previous_training",
                "Previous training history",
                f"Master history contains {previous_records} earlier course record(s).",
            )

        review_required = bool(flags)
        review_fingerprint = hashlib.sha256(
            json.dumps(
                [
                    {
                        "code": flag["code"],
                        "detail": flag["detail"],
                    }
                    for flag in flags
                ],
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest() if flags else ""
        stored_status = registration.duplicate_review_status
        if not review_required:
            review_state = "clear"
            review_label = "No duplicate found"
        elif (
            stored_status == registration.DuplicateReviewStatus.ALLOWED
            and registration.duplicate_review_fingerprint
            == review_fingerprint
        ):
            review_state = "allowed"
            review_label = "Reviewed — allowed"
        elif (
            stored_status == registration.DuplicateReviewStatus.EXCLUDED
            and registration.duplicate_review_fingerprint
            == review_fingerprint
        ):
            review_state = "excluded"
            review_label = "Reviewed — excluded"
        else:
            review_state = "required"
            review_label = "Review required"

        rows.append(
            {
                "serial": serial,
                "registration": registration,
                "eid_display": format_emirates_id(
                    registration.eid_normalized or registration.eid_raw
                ),
                "flags": flags,
                "warnings": [flag["detail"] for flag in flags],
                "review_required": review_required,
                "review_state": review_state,
                "review_label": review_label,
                "review_fingerprint": review_fingerprint,
                "can_select": review_state in {"clear", "allowed"},
                "previous_courses": previous_records,
                "previous_same_course": previous_same_course,
                "master_matches": matches["all"],
            }
        )
    return rows

