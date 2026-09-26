from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from .identity import format_emirates_id
from .models import Student


def eid_details(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 15:
        raise ValidationError("Emirates ID must contain exactly 15 digits.")
    if not digits.startswith("784"):
        raise ValidationError("Emirates ID must begin with 784.")
    year = int(digits[3:7])
    current_year = timezone.localdate().year
    if year < 1900 or year > current_year:
        raise ValidationError("The second Emirates ID section must be a valid birth year.")
    return digits, format_emirates_id(digits)


def validated_registration_values(registration):
    english_name = (registration.submitted_name_english or "").strip().upper()
    arabic_name = (registration.submitted_name_arabic or "").strip()
    email = (registration.email_raw or "").strip().lower()
    phone = (registration.phone_raw or "").strip()
    errors = []
    if not english_name:
        errors.append("English name is required.")
    if not arabic_name:
        errors.append("Arabic name is required.")
    try:
        validate_email(email)
    except ValidationError:
        errors.append("A valid email address is required.")
    try:
        eid_digits, eid_formatted = eid_details(
            registration.eid_normalized or registration.eid_raw
        )
    except ValidationError as exc:
        errors.extend(exc.messages)
        eid_digits = ""
        eid_formatted = ""
    if errors:
        raise ValidationError(errors)
    return {
        "english_name": english_name,
        "arabic_name": arabic_name,
        "email": email,
        "phone": phone,
        "eid_digits": eid_digits,
        "eid_formatted": eid_formatted,
    }


def find_or_create_student(registration):
    values = validated_registration_values(registration)
    possible_eids = {
        values["eid_digits"],
        values["eid_formatted"],
        (registration.eid_normalized or "").strip(),
        (registration.eid_raw or "").strip(),
    }
    possible_eids.discard("")
    matches = list(Student.objects.filter(eid__in=possible_eids).order_by("id")[:3])
    if len(matches) > 1:
        raise ValidationError(
            "More than one master Student matches this Emirates ID. Resolve the duplicate before selection."
        )
    if matches:
        return matches[0], False
    return (
        Student.objects.create(
            eid=values["eid_digits"],
            name_english=values["english_name"],
            name_arabic=values["arabic_name"],
            email=values["email"],
            phone=values["phone"],
        ),
        True,
    )


def registration_export_values(registration):
    student = registration.student
    email = ((student.email if student else "") or registration.email_raw or "").strip().lower()
    arabic_name = (
        (student.name_arabic if student else "")
        or registration.submitted_name_arabic
        or ""
    ).strip()
    eid_value = (
        (student.eid if student else "")
        or registration.eid_normalized
        or registration.eid_raw
    )
    errors = []
    try:
        validate_email(email)
    except ValidationError:
        errors.append("valid email")
    try:
        _, eid_formatted = eid_details(eid_value)
    except ValidationError:
        eid_formatted = ""
        errors.append("valid Emirates ID")
    if not arabic_name:
        errors.append("Arabic full name")
    return {
        "email": email,
        "emirates_id": eid_formatted,
        "full_name": arabic_name,
        "errors": errors,
    }

