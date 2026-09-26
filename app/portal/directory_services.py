"""Read-only directory rows shared by student lists and reporting.

A course participation is identified by student and session. A saved training
record represents the same participation as its registration, not a second
student. Separate sessions are deliberately retained as separate rows.
"""
from datetime import date

from django.db.models import Q

from .course_services import display_course_name
from .identity import format_emirates_id
from .models import CourseSession, Registration, Student, TrainingRecord


OUTCOME_CHOICES = (("pass", "Pass"), ("fail", "Fail"), ("rejected", "Rejected"),
                   ("pending", "TBC"), ("hp", "HP"), ("ttt", "TTT"))
LIFECYCLE_CHOICES = (("draft", "Draft"), ("confirmed", "Confirmed"),
                     ("in_progress", "In progress"), ("completed", "Completed"),
                     ("cancelled", "Cancelled"))


def valid_date(value):
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def filter_session_frame(sessions, params):
    """Include courses overlapping the requested calendar frame."""
    course = str(params.get("course", ""))
    if course.isdigit():
        sessions = sessions.filter(course_id=int(course))
    lower, upper = valid_date(params.get("date_from")), valid_date(params.get("date_to"))
    if lower and upper and lower > upper:
        lower, upper = upper, lower
    if lower:
        sessions = sessions.filter(Q(end_date__gte=lower) | Q(end_date__isnull=True, start_date__gte=lower))
    if upper:
        sessions = sessions.filter(start_date__lte=upper)
    return sessions


def filter_lifecycle(sessions, value):
    if value == "confirmed":
        return sessions.filter(status__in=["registration_open", "registration_closed"])
    if value in dict(LIFECYCLE_CHOICES):
        return sessions.filter(status=value)
    return sessions


def participation_rows(*, students=None, sessions=None, include_unlinked=False, include_empty=False, valid_only=False):
    records = TrainingRecord.objects.select_related("student", "session__course", "session__camp")
    registrations = Registration.objects.select_related("student", "requested_session__course", "requested_session__camp")
    if valid_only:
        records = records.filter(duplicate_flag=False).exclude(record_status="cancelled")
        registrations = registrations.filter(selected_for_roster=True).exclude(status="rejected").exclude(duplicate_review_status="reviewed_exclude")
    if students is not None:
        records = records.filter(student__in=students)
        registrations = registrations.filter(student__in=students)
    elif not include_unlinked:
        registrations = registrations.filter(student__isnull=False)
    if sessions is not None:
        records = records.filter(session__in=sessions)
        registrations = registrations.filter(requested_session__in=sessions)

    rows, registration_keys = {}, {}
    for registration in registrations.order_by("updated_at", "pk"):
        student, session = registration.student, registration.requested_session
        key = (student.pk, session.pk if session else None) if student else ("registration", registration.pk)
        rejected = (registration.status == "rejected" or registration.duplicate_review_status == "reviewed_exclude")
        outcome = "rejected" if rejected else {"passed": "pass", "failed": "fail"}.get(registration.assessment_status, "pending")
        rows[key] = _row(student, session, registration, outcome)
        rows[key].update({
            "row_key": f"r{registration.pk}", "registration": registration,
            "enrolled": registration.selected_for_roster and not rejected,
            "day1": registration.day1_attended, "day2": registration.day2_attended,
            "attendance": "S1 + S2" if registration.day1_attended and registration.day2_attended else
                          "S1" if registration.day1_attended else "S2" if registration.day2_attended else "Not recorded",
        })
        registration_keys[str(registration.public_id)] = key
    for record in records.order_by("updated_at", "pk"):
        key = (record.student_id, record.session_id)
        previous = rows.get(key, {})
        payload = record.raw_payload if isinstance(record.raw_payload, dict) else {}
        source_key = registration_keys.get(str(payload.get("registration_public_id", "")))
        if source_key is not None and source_key != key:
            previous = rows.pop(source_key, previous)
        outcome = record.result if record.result in {"pass", "fail"} else "pending"
        row = _row(record.student, record.session, record, outcome)
        row.update({
            "row_key": f"t{record.pk}", "record": record,
            "registration": previous.get("registration"),
            "enrolled": not record.duplicate_flag and record.record_status != "cancelled",
            "day1": previous.get("day1", False), "day2": previous.get("day2", False),
            "attendance": record.get_attendance_display(),
            "duplicate": record.duplicate_flag,
        })
        rows[key] = row
    result = list(rows.values())
    if include_empty and students is not None:
        represented = {row["student"].pk for row in result if row["student"]}
        for student in students:
            if student.pk not in represented:
                row = _row(student, None, student, "pending")
                row.update({"row_key": f"s{student.pk}", "enrolled": False, "attendance": "—"})
                result.append(row)
    return result


def _row(student, session, source, outcome):
    english = student.name_english if student else source.submitted_name_english
    arabic = student.name_arabic if student else source.submitted_name_arabic
    eid = student.eid if student else source.eid_normalized or source.eid_raw
    return {
        "student": student, "session": session,
        "name_english": english, "name_arabic": arabic,
        "email": student.email if student else source.email_raw,
        "formatted_eid": format_emirates_id(eid),
        "outcome": outcome, "outcome_label": dict(OUTCOME_CHOICES).get(outcome, "TBC"),
        "is_hp": bool(source.is_hp), "is_ttt": bool(source.is_ttt),
        "course_name": display_course_name(session) if session else "No course recorded",
        "date": session.start_date if session else None,
        "quality": student.identity_status if student else "review",
        "quality_label": {"verified": "Verified", "review": "Review needed", "invalid_id": "Invalid Emirates ID"}.get(student.identity_status, "Review needed") if student else "Review needed",
    }


def rejected_participation_rows(sessions):
    """Keep excluded/rejected submissions visible without enrolling them."""
    registrations = Registration.objects.filter(requested_session__in=sessions).filter(
        Q(status="rejected") | Q(duplicate_review_status="reviewed_exclude")
    ).select_related("student", "requested_session__course", "requested_session__camp")
    rows = []
    for registration in registrations:
        row = _row(registration.student, registration.requested_session, registration, "rejected")
        row.update({"row_key": f"r{registration.pk}", "enrolled": False, "registration": registration})
        rows.append(row)
    return rows


def filtered_student_rows(params):
    students = Student.objects.all()
    query = str(params.get("q", "")).strip()
    if query:
        students = students.filter(Q(name_english__icontains=query) | Q(name_arabic__icontains=query) |
                                   Q(eid__icontains=query) | Q(email__icontains=query) | Q(phone__icontains=query))
    identity = params.get("identity_status", "")
    active = params.get("active", "yes")
    if active in {"yes", "no"}:
        students = students.filter(active=active == "yes")
    sessions = filter_session_frame(CourseSession.objects.all(), params)
    course_filter = str(params.get("course", "")).isdigit()
    dated = valid_date(params.get("date_from")) or valid_date(params.get("date_to"))
    rows = participation_rows(students=students, sessions=sessions, include_empty=not (course_filter or dated))
    # Keep identity checks and duplicate review visible together without
    # modifying the student's identity status or course result in storage.
    from .duplicate_services import build_registration_review_rows
    review_session_ids = {r["registration"].requested_session_id for r in rows if r.get("registration")}
    registrations = list(Registration.objects.filter(requested_session_id__in=review_session_ids).select_related(
        "student", "requested_session__course", "requested_session__camp", "duplicate_reviewed_by"))
    reviews = {r["registration"].pk: r for r in build_registration_review_rows(registrations)}
    for row in rows:
        registration = row.get("registration")
        review = reviews.get(registration.pk) if registration else None
        row["duplicate_review"] = review if review and review["review_required"] else None
        if (review and review["review_state"] == "required") or row.get("duplicate"):
            if row["quality"] != "invalid_id":
                row["quality"], row["quality_label"] = "review", "Review needed"
    if identity in Student.IdentityStatus.values:
        rows = [r for r in rows if r["quality"] == identity]
    if params.get("duplicates") == "yes":
        rows = [r for r in rows if r.get("duplicate_review") or r.get("duplicate")]
    outcome = params.get("outcome", "")
    if outcome in {"hp", "ttt"}:
        rows = [row for row in rows if row[f"is_{outcome}"]]
    elif outcome in dict(OUTCOME_CHOICES):
        rows = [row for row in rows if row["outcome"] == outcome]
    sort = params.get("sort", "name")
    if sort == "eid":
        rows.sort(key=lambda row: (row["formatted_eid"], row["name_english"].casefold(), row["row_key"]))
    elif sort == "newest":
        rows.sort(key=lambda row: (row["date"] or date.min, row["student"].created_at, row["row_key"]), reverse=True)
    else:
        rows.sort(key=lambda row: (row["name_english"].casefold(), row["name_arabic"], row["date"] or date.min, row["row_key"]), reverse=sort == "name_desc")
    return rows


def session_totals(sessions):
    """Count canonical enrolled participations; never add training + registration."""
    sessions = list(sessions)
    totals = {session.pk: dict(enrolled=0, passed=0, failed=0, rejected=0, pending=0, hp=0, ttt=0) for session in sessions}
    for row in participation_rows(sessions=sessions, include_unlinked=True, valid_only=True):
        if row["session"] is None:
            continue
        values = totals[row["session"].pk]
        if not row["enrolled"]:
            continue
        values["enrolled"] += 1
        values[{"pass": "passed", "fail": "failed"}.get(row["outcome"], "pending")] += 1
        values["hp"] += int(row["is_hp"])
        values["ttt"] += int(row["is_ttt"])
    # Rejected registration submissions remain reportable even when an older
    # training record exists for the same person and session.
    for session_id in Registration.objects.filter(requested_session__in=sessions).filter(
        Q(status="rejected") | Q(duplicate_review_status="reviewed_exclude")
    ).values_list("requested_session_id", flat=True):
        totals[session_id]["rejected"] += 1
    return totals
