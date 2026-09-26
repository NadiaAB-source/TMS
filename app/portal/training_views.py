
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .identity import format_emirates_id
from .models import TrainingRecord


def _field(model, candidates, field_type=None):
    fields = {
        field.name: field
        for field in model._meta.get_fields()
        if getattr(field, "concrete", False)
    }

    for name in candidates:
        field = fields.get(name)
        if field and (field_type is None or isinstance(field, field_type)):
            return name

    return None


def _value(obj, candidates, default=None):
    if obj is None:
        return default

    for name in candidates:
        try:
            value = getattr(obj, name)
        except Exception:
            continue

        if value not in (None, ""):
            return value

    return default


def _display_value(obj, candidates, default="—"):
    if obj is None:
        return default

    for name in candidates:
        try:
            value = getattr(obj, name)
        except Exception:
            continue

        if value in (None, ""):
            continue

        display_method = getattr(obj, f"get_{name}_display", None)
        if callable(display_method):
            try:
                displayed = display_method()
                if displayed not in (None, ""):
                    return str(displayed)
            except Exception:
                pass

        return str(value)

    return default


def _person_name(person):
    if person is None:
        return "—"

    full_name = _value(
        person,
        (
            "full_name",
            "name",
            "student_name",
            "english_name",
            "name_en",
            "candidate_name",
        ),
    )

    if full_name:
        return str(full_name)

    first_name = _value(person, ("first_name",), "")
    last_name = _value(person, ("last_name",), "")
    combined = f"{first_name} {last_name}".strip()

    return combined or str(person)


def _boolean_display(value):
    if value is True:
        return "Yes"
    if value is False:
        return "No"

    normalized = str(value or "").strip().lower()

    if normalized in {"yes", "true", "1", "y"}:
        return "Yes"
    if normalized in {"no", "false", "0", "n"}:
        return "No"

    return "—"


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
            depth < 2
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


STATUS_FIELDS = ("attendance_status", "status", "attendance")
RESULT_FIELDS = ("result_status", "result", "outcome")
HP_FIELDS = ("is_hp", "hp", "high_performer", "high_potential")
TTT_FIELDS = ("is_ttt", "ttt", "train_the_trainer", "ttt_candidate")


def _prepare_queryset(parameters):
    queryset = TrainingRecord.objects.all()

    related_fields = [
        field.name
        for field in TrainingRecord._meta.get_fields()
        if (
            getattr(field, "concrete", False)
            and (
                getattr(field, "many_to_one", False)
                or getattr(field, "one_to_one", False)
            )
        )
    ]

    if related_fields:
        queryset = queryset.select_related(*related_fields)

    search_text = str(parameters.get("q", "")).strip()

    if search_text:
        combined_query = Q()

        for lookup in _text_lookups(TrainingRecord):
            combined_query |= Q(**{f"{lookup}__icontains": search_text})

        queryset = queryset.filter(combined_query)

    status_field = _field(
        TrainingRecord,
        STATUS_FIELDS,
        (models.CharField, models.TextField),
    )
    result_field = _field(
        TrainingRecord,
        RESULT_FIELDS,
        (models.CharField, models.TextField),
    )
    hp_field = _field(TrainingRecord, HP_FIELDS)
    ttt_field = _field(TrainingRecord, TTT_FIELDS)

    status_value = str(parameters.get("status", "")).strip()
    result_value = str(parameters.get("result", "")).strip()
    hp_value = str(parameters.get("hp", "")).strip().lower()
    ttt_value = str(parameters.get("ttt", "")).strip().lower()

    if status_field and status_value:
        queryset = queryset.filter(**{status_field: status_value})

    if result_field and result_value:
        queryset = queryset.filter(**{result_field: result_value})

    if hp_field and hp_value in {"yes", "no"}:
        field_object = TrainingRecord._meta.get_field(hp_field)

        if isinstance(field_object, models.BooleanField):
            queryset = queryset.filter(**{hp_field: hp_value == "yes"})
        else:
            accepted = ["yes", "true", "1"] if hp_value == "yes" else ["no", "false", "0"]
            condition = Q()

            for accepted_value in accepted:
                condition |= Q(**{f"{hp_field}__iexact": accepted_value})

            queryset = queryset.filter(condition)

    if ttt_field and ttt_value in {"yes", "no"}:
        field_object = TrainingRecord._meta.get_field(ttt_field)

        if isinstance(field_object, models.BooleanField):
            queryset = queryset.filter(**{ttt_field: ttt_value == "yes"})
        else:
            accepted = ["yes", "true", "1"] if ttt_value == "yes" else ["no", "false", "0"]
            condition = Q()

            for accepted_value in accepted:
                condition |= Q(**{f"{ttt_field}__iexact": accepted_value})

            queryset = queryset.filter(condition)

    concrete_fields = {
        field.name for field in TrainingRecord._meta.get_fields()
        if getattr(field, "concrete", False)
    }

    if "created_at" in concrete_fields:
        queryset = queryset.order_by("-created_at")
    elif "id" in concrete_fields:
        queryset = queryset.order_by("-id")

    return queryset.distinct(), status_field, result_field, hp_field, ttt_field


def _record_row(record):
    student = _value(record, ("student", "participant", "trainee"))
    session = _value(record, ("course_session", "session"))
    course = _value(session, ("course",)) if session else None

    if course is None:
        course = _value(record, ("course",))

    camp = _value(session, ("camp", "location")) if session else None
    instructor = _value(
        session,
        ("lead_instructor", "instructor", "primary_instructor"),
    ) if session else None

    session_date = _value(
        session,
        ("start_date", "course_date", "date", "date_from"),
    ) if session else None

    emirates_id = _value(
        student,
        ("emirates_id", "eid", "national_id", "identity_number"),
        "—",
    )

    reference = _value(record, ("public_id", "uuid", "id", "pk"), record.pk)

    return {
        "id": str(reference),
        "student": _person_name(student),
        "emirates_id": format_emirates_id(emirates_id),
        "course": _display_value(
            course,
            ("title", "name", "course_name", "code"),
            str(course) if course else "—",
        ),
        "date": str(session_date or "—"),
        "camp": str(camp or "—"),
        "instructor": _person_name(instructor),
        "status": _display_value(record, STATUS_FIELDS),
        "result": _display_value(record, RESULT_FIELDS),
        "hp": _boolean_display(_value(record, HP_FIELDS)),
        "ttt": _boolean_display(_value(record, TTT_FIELDS)),
    }


def _distinct_choices(queryset, field_name):
    if not field_name:
        return []

    values = (
        queryset.order_by()
        .exclude(**{f"{field_name}__isnull": True})
        .exclude(**{field_name: ""})
        .values_list(field_name, flat=True)
        .distinct()
    )

    return sorted(
        [(str(value), str(value)) for value in values],
        key=lambda item: item[1].lower(),
    )


@login_required
def training_record_list(request):
    unfiltered = TrainingRecord.objects.all()
    queryset, status_field, result_field, hp_field, ttt_field = _prepare_queryset(
        request.GET
    )

    try:
        per_page = int(request.GET.get("per_page", 50))
    except (TypeError, ValueError):
        per_page = 50

    if per_page not in {25, 50, 100}:
        per_page = 50

    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = [_record_row(record) for record in page_obj.object_list]

    query_copy = request.GET.copy()
    query_copy.pop("page", None)

    hidden_filters = [
        (key, value)
        for key, values in request.GET.lists()
        if key != "page"
        for value in values
    ]

    context = {
        "active_module": "training_records",
        "page_obj": page_obj,
        "rows": rows,
        "total_records": unfiltered.count(),
        "filtered_count": queryset.count(),
        "status_choices": _distinct_choices(unfiltered, status_field),
        "result_choices": _distinct_choices(unfiltered, result_field),
        "has_hp": bool(hp_field),
        "has_ttt": bool(ttt_field),
        "per_page": per_page,
        "query_without_page": query_copy.urlencode(),
        "hidden_filters": hidden_filters,
        "current": {
            "q": request.GET.get("q", ""),
            "status": request.GET.get("status", ""),
            "result": request.GET.get("result", ""),
            "hp": request.GET.get("hp", ""),
            "ttt": request.GET.get("ttt", ""),
        },
    }

    return render(request, "portal/training_record_list.html", context)


@login_required
@require_POST
def training_record_print(request):
    scope = request.POST.get("scope", "selected")
    queryset, _, _, _, _ = _prepare_queryset(request.POST)

    if scope == "selected":
        selected_ids = [
            value.strip()
            for value in request.POST.getlist("record_ids")
            if value.strip()
        ]

        if not selected_ids:
            return HttpResponseBadRequest(
                "No training records were selected. Return to the previous page and select at least one record."
            )

        identifier_field = _field(
            TrainingRecord,
            ("public_id", "uuid", "id"),
        ) or "pk"

        queryset = queryset.filter(
            **{f"{identifier_field}__in": selected_ids}
        )

    rows = [_record_row(record) for record in queryset]

    return render(
        request,
        "portal/training_record_print.html",
        {
            "rows": rows,
            "scope": scope,
        },
    )
