"""Read-only projection of historical evidence for the dashboard.

The historical migration deliberately kept evidence in :class:`SourceRecord`
instead of inventing final ``TrainingRecord`` rows.  The dashboard still needs
to report that evidence, but it must do so without mutating or silently
approving the migration data.  This module turns the supported evidence types
into conservative, deduplicated participation facts for counters and the
monthly chart.

The projection observes these boundaries:

* a registration is evidence of enrolment, not evidence of attendance or pass;
* attendance and an explicit result for the same person/course are one
  participation;
* only literal PASS, FAIL and TTT result tokens are interpreted;
* ambiguous dates, course links and instructor aliases are never guessed;
* evidence already linked to a canonical session/training participation is
  omitted so the caller may safely add this projection to canonical totals;
* instructor access is granted only by a direct link or an approved instructor
  alias.  Merely similar or proposed names do not grant access.

No function in this module writes to the database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import re
import unicodedata
from typing import Any, Iterable

from django.db.models import Prefetch

from .models import (
    Course,
    CourseSessionProposal,
    EntityAliasProposal,
    Instructor,
    Registration,
    SourceRecord,
    Team,
    TrainingRecord,
)


HISTORICAL_PARTICIPATION_TYPES = (
    "registration_submission",
    "course_attendance_evidence",
    "course_result_evidence",
    "ttt_candidate_evidence",
)


@dataclass(frozen=True)
class HistoricalDashboardFact:
    """One deduplicated historical course participation."""

    identity_key: str
    group_key: str
    event_date: date | None
    course_id: int | None
    enrolled: bool
    result: str | None
    is_rejected: bool
    is_ttt: bool
    result_conflict: bool
    source_types: tuple[str, ...]
    source_record_ids: tuple[int, ...]


@dataclass(frozen=True)
class HistoricalDashboardProjection:
    """Counter and chart additions derived from historical evidence."""

    facts: tuple[HistoricalDashboardFact, ...]
    totals: dict[str, int]
    month_values: dict[int, dict[str, int]]

    @classmethod
    def empty(cls) -> "HistoricalDashboardProjection":
        return cls(
            facts=(),
            totals={
                "courses": 0,
                "enrolled": 0,
                "passed": 0,
                "failed": 0,
                "rejected": 0,
                "hp": 0,
                "ttt": 0,
            },
            month_values=_empty_month_values(),
        )


@dataclass(frozen=True)
class _Candidate:
    source_record_id: int
    source_type: str
    identity_key: str
    group_key: str
    event_date: date | None
    course_id: int | None
    instructor_ids: frozenset[int]
    team_ids: frozenset[int]
    enrolled: bool
    result: str | None
    is_rejected: bool
    is_ttt: bool
    has_course_context: bool
    has_canonical_link: bool


def project_historical_dashboard(
    *,
    administrator: bool,
    instructor: Instructor | None,
    date_from: date | None,
    date_to: date | None,
    all_history: bool,
    selected_course: Course | int | str | None = None,
    selected_team: Team | int | str | None = None,
    selected_instructor: Instructor | int | str | None = None,
) -> HistoricalDashboardProjection:
    """Project eligible ``SourceRecord`` rows into dashboard additions.

    ``selected_course`` and ``selected_team`` accept a model instance, integer
    primary key, numeric string or ``None``.  Unknown historical course/team
    mappings are excluded when the corresponding filter is active.  Likewise,
    undated evidence is included only for ``all_history=True``; it cannot be
    truthfully placed inside a bounded date range.

    The returned values are *additions*.  They intentionally omit historical
    evidence with a canonical session/training link, so callers should add the
    totals/month values to their existing canonical calculation.
    """

    if not administrator and instructor is None:
        return HistoricalDashboardProjection.empty()

    course_id = _object_id(selected_course)
    team_id = _object_id(selected_team)
    instructor_id = _object_id(selected_instructor)
    inferred_course_id = _single_tccc_asm_course_id()

    aliases = list(
        EntityAliasProposal.objects.filter(
            alias_type=EntityAliasProposal.AliasType.INSTRUCTOR,
            proposal_status=EntityAliasProposal.ProposalStatus.APPROVED,
            proposed_instructor__isnull=False,
        )
        .select_related("proposed_instructor__team")
    )
    alias_targets = _approved_alias_targets(aliases)

    approved_alias_prefetch = Prefetch(
        "entity_alias_proposals",
        queryset=(
            EntityAliasProposal.objects.filter(
                alias_type=EntityAliasProposal.AliasType.INSTRUCTOR,
                proposal_status=(
                    EntityAliasProposal.ProposalStatus.APPROVED
                ),
                proposed_instructor__isnull=False,
            ).select_related("proposed_instructor__team")
        ),
        to_attr="approved_dashboard_instructor_aliases",
    )
    proposal_prefetch = Prefetch(
        "course_session_proposals",
        queryset=CourseSessionProposal.objects.select_related(
            "proposed_course",
            "proposed_camp",
            "approved_session__course",
        ).order_by("id"),
        to_attr="dashboard_course_proposals",
    )
    records = list(
        SourceRecord.objects.filter(
            record_type__in=HISTORICAL_PARTICIPATION_TYPES,
        )
        .exclude(import_status=SourceRecord.ImportStatus.IGNORED)
        .select_related(
            "linked_camp",
            "linked_course",
            "linked_instructor__team",
            "linked_student",
            "linked_session__course",
            "linked_registration__requested_session__course",
            "linked_training_record__session__course",
        )
        .prefetch_related(proposal_prefetch, approved_alias_prefetch)
        .order_by("id")
    )

    candidates: list[_Candidate] = []
    for record in records:
        proposals = list(record.dashboard_course_proposals)
        proposal = (
            proposals[0]
            if (
                len(proposals) == 1
                and proposals[0].proposal_status
                != CourseSessionProposal.ProposalStatus.REJECTED
            )
            else None
        )
        candidates.append(
            _candidate_from_record(
                record,
                proposal,
                alias_targets,
                inferred_course_id,
            )
        )

    # If one evidence row in a participation has a canonical link, suppress the
    # whole historical participation.  This also prevents an unlinked
    # attendance companion row from duplicating a linked result row.
    canonical_keys = {
        (candidate.identity_key, candidate.group_key)
        for candidate in candidates
        if candidate.has_canonical_link
    }

    eligible: list[_Candidate] = []
    for candidate in candidates:
        participation_key = (
            candidate.identity_key,
            candidate.group_key,
        )
        if participation_key in canonical_keys:
            continue
        if not _can_view_candidate(
            candidate,
            administrator=administrator,
            instructor=instructor,
        ):
            continue
        if team_id is not None and team_id not in candidate.team_ids:
            continue
        if instructor_id is not None and instructor_id not in candidate.instructor_ids:
            continue
        if course_id is not None and candidate.course_id != course_id:
            continue
        if not all_history:
            if candidate.event_date is None:
                continue
            if date_from is not None and candidate.event_date < date_from:
                continue
            if date_to is not None and candidate.event_date > date_to:
                continue
        # TTT rows are student flags until they can be placed in a dated or
        # grouped course context.  Do not turn a context-free flag into an
        # enrolment count.
        if (
            candidate.source_type == "ttt_candidate_evidence"
            and not candidate.has_course_context
        ):
            continue
        eligible.append(candidate)

    facts = _merge_candidates(eligible)
    return _projection_from_facts(facts)


def _candidate_from_record(
    record: SourceRecord,
    proposal: CourseSessionProposal | None,
    alias_targets: dict[str, set[tuple[int, int | None]]],
    inferred_course_id: int | None,
) -> _Candidate:
    raw = record.raw_payload if isinstance(record.raw_payload, dict) else {}
    event_date = None
    if record.linked_training_record_id and record.linked_training_record:
        event_date = record.linked_training_record.session.start_date
    elif record.linked_session_id and record.linked_session:
        event_date = record.linked_session.start_date
    elif (
        record.linked_registration_id
        and record.linked_registration
        and record.linked_registration.requested_session_id
    ):
        event_date = (
            record.linked_registration.requested_session.start_date
        )
    elif proposal is not None:
        event_date = proposal.start_date
    if event_date is None:
        event_date = _raw_event_date(raw)

    course_id = (
        _resolved_course_id(record, proposal)
        or inferred_course_id
    )
    camp_key = _resolved_camp_key(record, proposal, raw)
    unit_key = _normalize_text(_raw_field(raw, "unit"))
    group_key, has_group = _group_key(
        record,
        proposal,
        event_date,
        course_id,
        camp_key,
        unit_key,
    )
    instructor_ids, team_ids = _resolved_instructors(
        record,
        proposal,
        raw,
        alias_targets,
    )

    result = None
    is_ttt = record.record_type == "ttt_candidate_evidence"
    if record.record_type == "course_result_evidence":
        result, result_ttt = _explicit_result(raw)
        is_ttt = is_ttt or result_ttt

    linked_training = record.linked_training_record
    canonical_training = bool(
        linked_training is not None
        and not linked_training.duplicate_flag
        and linked_training.record_status
        != TrainingRecord.RecordStatus.CANCELLED
    )
    linked_registration = record.linked_registration
    is_rejected = bool(
        record.record_type == "registration_submission"
        and linked_registration is not None
        and (
            linked_registration.status == Registration.Status.REJECTED
            or linked_registration.duplicate_review_status
            == Registration.DuplicateReviewStatus.EXCLUDED
        )
    )
    canonical_registration = bool(
        linked_registration is not None
        and linked_registration.requested_session_id
        and linked_registration.selected_for_roster
        and linked_registration.status != Registration.Status.REJECTED
        and linked_registration.duplicate_review_status
        != Registration.DuplicateReviewStatus.EXCLUDED
    )
    canonical_rejection = bool(
        linked_registration is not None
        and linked_registration.requested_session_id
        and is_rejected
    )
    has_canonical_link = (
        canonical_training
        or canonical_registration
        or canonical_rejection
    )

    return _Candidate(
        source_record_id=record.id,
        source_type=record.record_type,
        identity_key=_identity_key(record, raw),
        group_key=group_key,
        event_date=event_date,
        course_id=course_id,
        instructor_ids=frozenset(instructor_ids),
        team_ids=frozenset(team_ids),
        # TTT evidence is an explicit student classification with course
        # context, but it is not proof of attendance.  It belongs only in the
        # TTT counter unless separate attendance/result evidence exists.
        enrolled=(
            record.record_type != "ttt_candidate_evidence"
            and not is_rejected
        ),
        result=result,
        is_rejected=is_rejected,
        is_ttt=is_ttt,
        has_course_context=bool(event_date or has_group),
        has_canonical_link=has_canonical_link,
    )


def _resolved_course_id(
    record: SourceRecord,
    proposal: CourseSessionProposal | None,
) -> int | None:
    if (
        record.linked_training_record_id
        and record.linked_training_record is not None
    ):
        return record.linked_training_record.session.course_id
    if record.linked_course_id:
        return record.linked_course_id
    if record.linked_session_id and record.linked_session is not None:
        return record.linked_session.course_id
    registration = record.linked_registration
    if (
        registration is not None
        and registration.requested_session_id
        and registration.requested_session is not None
    ):
        return registration.requested_session.course_id
    if proposal is not None:
        if proposal.proposed_course_id:
            return proposal.proposed_course_id
        if proposal.approved_session_id and proposal.approved_session:
            return proposal.approved_session.course_id
    return None


def _resolved_camp_key(
    record: SourceRecord,
    proposal: CourseSessionProposal | None,
    raw: dict[str, Any],
) -> str:
    if record.linked_camp_id and record.linked_camp is not None:
        return f"camp:{record.linked_camp_id}"
    if proposal is not None:
        if proposal.proposed_camp_id:
            return f"camp:{proposal.proposed_camp_id}"
        values = proposal.camp_values
        if isinstance(values, list):
            normalized = sorted(
                {_normalize_text(value) for value in values if value}
            )
            if normalized:
                return "|".join(normalized)
    value = _raw_field(raw, "camp") or _raw_field(raw, "camp", "name")
    return _normalize_text(value)


def _group_key(
    record: SourceRecord,
    proposal: CourseSessionProposal | None,
    event_date: date | None,
    course_id: int | None,
    camp_key: str,
    unit_key: str,
) -> tuple[str, bool]:
    # A registration submission remains its own preserved evidence row until
    # duplicate review explicitly excludes it.  Do not silently collapse two
    # submissions merely because their identity and proposed course match.
    if record.record_type == "registration_submission":
        identity = record.linked_registration_id or record.id
        return f"registration:{identity}", True
    if proposal is not None:
        return f"proposal:{proposal.id}", True
    if record.linked_session_id:
        return f"session:{record.linked_session_id}", True
    if course_id is not None or event_date is not None or camp_key or unit_key:
        return (
            "context:"
            f"{course_id or ''}|{event_date.isoformat() if event_date else ''}|"
            f"{camp_key}|{unit_key}",
            True,
        )
    return f"source:{record.id}", False


def _identity_key(record: SourceRecord, raw: dict[str, Any]) -> str:
    derived = (
        record.derived_search_values
        if isinstance(record.derived_search_values, dict)
        else {}
    )
    eid = _digits(derived.get("normalized_emirates_id"))
    if (
        not eid
        and record.linked_student_id
        and record.linked_student is not None
    ):
        eid = _digits(record.linked_student.eid)
    if not eid:
        eid = _digits(
            _raw_field(raw, "emirates", "id")
            or _raw_field(raw, "id", "number")
            or raw.get("eid")
        )
    if eid:
        return f"eid:{eid}"
    if record.linked_student_id:
        return f"student:{record.linked_student_id}"

    email = (
        derived.get("normalized_email")
        or _raw_field(raw, "email", "address")
        or raw.get("email")
    )
    normalized_email = str(_decoded(email) or "").strip().casefold()
    if normalized_email:
        return f"email:{normalized_email}"
    if record.linked_registration_id:
        return f"registration:{record.linked_registration_id}"
    return f"source:{record.id}"


def _resolved_instructors(
    record: SourceRecord,
    proposal: CourseSessionProposal | None,
    raw: dict[str, Any],
    alias_targets: dict[str, set[tuple[int, int | None]]],
) -> tuple[set[int], set[int]]:
    instructor_ids: set[int] = set()
    team_ids: set[int] = set()

    if record.linked_instructor_id and record.linked_instructor is not None:
        instructor_ids.add(record.linked_instructor_id)
        if record.linked_instructor.team_id:
            team_ids.add(record.linked_instructor.team_id)
        # A reviewed direct link is authoritative.  Stale raw names or older
        # alias proposals must not expose the same record to another person.
        return instructor_ids, team_ids

    for alias in getattr(
        record,
        "approved_dashboard_instructor_aliases",
        (),
    ):
        if alias.proposed_instructor_id:
            instructor_ids.add(alias.proposed_instructor_id)
            if alias.proposed_instructor.team_id:
                team_ids.add(alias.proposed_instructor.team_id)

    values: list[Any] = []
    if proposal is not None and isinstance(proposal.instructor_values, list):
        values.extend(proposal.instructor_values)
    values.extend(_raw_instructor_values(raw))
    for value in values:
        for target_id, target_team_id in alias_targets.get(
            _normalize_text(value),
            (),
        ):
            instructor_ids.add(target_id)
            if target_team_id:
                team_ids.add(target_team_id)
    return instructor_ids, team_ids


def _approved_alias_targets(
    aliases: Iterable[EntityAliasProposal],
) -> dict[str, set[tuple[int, int | None]]]:
    targets: dict[str, set[tuple[int, int | None]]] = defaultdict(set)
    for alias in aliases:
        if not alias.proposed_instructor_id:
            continue
        key = alias.normalized_source or _normalize_text(alias.source_value)
        targets[_normalize_text(key)].add(
            (
                alias.proposed_instructor_id,
                alias.proposed_instructor.team_id,
            )
        )
    return targets


def _raw_instructor_values(raw: dict[str, Any]) -> list[Any]:
    values = []
    for key, value in raw.items():
        normalized = _normalize_text(key)
        if (
            normalized == "instructor"
            or normalized.startswith("instructor ")
            or normalized == "instructors"
        ) and _decoded(value):
            values.append(_decoded(value))
    return values


def _can_view_candidate(
    candidate: _Candidate,
    *,
    administrator: bool,
    instructor: Instructor | None,
) -> bool:
    if administrator:
        return True
    return bool(
        instructor is not None
        and instructor.id in candidate.instructor_ids
    )


def _merge_candidates(
    candidates: Iterable[_Candidate],
) -> tuple[HistoricalDashboardFact, ...]:
    groups: dict[tuple[str, str], list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        groups[(candidate.identity_key, candidate.group_key)].append(candidate)

    facts = []
    for (identity_key, group_key), rows in sorted(groups.items()):
        results = {row.result for row in rows if row.result in {"pass", "fail"}}
        result_conflict = len(results) > 1
        result = next(iter(results)) if len(results) == 1 else None
        dates = {row.event_date for row in rows if row.event_date is not None}
        event_date = next(iter(dates)) if len(dates) == 1 else None
        course_ids = {row.course_id for row in rows if row.course_id is not None}
        course_id = next(iter(course_ids)) if len(course_ids) == 1 else None
        facts.append(
            HistoricalDashboardFact(
                identity_key=identity_key,
                group_key=group_key,
                event_date=event_date,
                course_id=course_id,
                enrolled=any(row.enrolled for row in rows),
                result=result,
                is_rejected=any(row.is_rejected for row in rows),
                is_ttt=any(row.is_ttt for row in rows),
                result_conflict=result_conflict,
                source_types=tuple(sorted({row.source_type for row in rows})),
                source_record_ids=tuple(
                    sorted({row.source_record_id for row in rows})
                ),
            )
        )
    return tuple(facts)


def _projection_from_facts(
    facts: tuple[HistoricalDashboardFact, ...],
) -> HistoricalDashboardProjection:
    month_values = _empty_month_values()
    totals = {
        # Course-session proposals are not confirmed courses.  The caller's
        # canonical CourseSession queryset remains the sole course counter.
        "courses": 0,
        "enrolled": sum(fact.enrolled for fact in facts),
        "passed": sum(fact.result == "pass" for fact in facts),
        "failed": sum(fact.result == "fail" for fact in facts),
        "rejected": sum(fact.is_rejected for fact in facts),
        "hp": 0,
        # TTT describes students, not course attempts.  The same student may
        # be supported by more than one historical evidence row.
        "ttt": len({fact.identity_key for fact in facts if fact.is_ttt}),
    }
    for fact in facts:
        if fact.event_date is None:
            continue
        month = month_values[fact.event_date.month]
        month["enrolled"] += int(fact.enrolled)
        month["passed"] += int(fact.result == "pass")
        month["failed"] += int(fact.result == "fail")
        month["rejected"] += int(fact.is_rejected)
    return HistoricalDashboardProjection(
        facts=facts,
        totals=totals,
        month_values=month_values,
    )


def _empty_month_values() -> dict[int, dict[str, int]]:
    return {
        month: {
            "enrolled": 0,
            "passed": 0,
            "failed": 0,
            "rejected": 0,
        }
        for month in range(1, 13)
    }


def _explicit_result(raw: dict[str, Any]) -> tuple[str | None, bool]:
    comment = _decoded(raw.get("comment"))
    if comment is None:
        comment = _raw_field(raw, "result")
    tokens = set(re.findall(r"[A-Z]+", str(comment or "").upper()))
    pass_seen = "PASS" in tokens or "PASSED" in tokens
    fail_seen = "FAIL" in tokens or "FAILED" in tokens
    result = None
    if pass_seen != fail_seen:
        result = "pass" if pass_seen else "fail"
    return result, "TTT" in tokens


def _raw_event_date(raw: dict[str, Any]) -> date | None:
    value = (
        raw.get("training_date")
        or _raw_field(raw, "training", "date")
        or _raw_field(raw, "date")
    )
    value = _decoded(value)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None
    # The authoritative TTT workbook also uses values such as ``16-Jul_26``
    # and ``10-August_26``.  The two-digit year is explicit; preserve it as
    # written rather than silently forcing every row into 2026.
    for pattern in ("%d-%b_%y", "%d-%B_%y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _single_tccc_asm_course_id() -> int | None:
    """Return the one unambiguous course represented by these source types.

    The four supported evidence types all come from the authoritative
    TCCC-ASM migration set.  If the live database has exactly one matching
    course, that exact match lets the existing Course filter include history.
    Multiple matches remain unresolved and are not guessed.
    """

    matches = []
    for course in Course.objects.only("id", "code", "title_english"):
        text = _normalize_text(f"{course.code} {course.title_english}")
        tokens = set(text.split())
        if "asm" in tokens and "tccc" in tokens:
            matches.append(course.id)
    return matches[0] if len(matches) == 1 else None


def _raw_field(raw: dict[str, Any], *needles: str) -> Any:
    normalized_needles = tuple(_normalize_text(needle) for needle in needles)
    for key, value in raw.items():
        normalized_key = _normalize_text(key)
        if all(needle in normalized_key for needle in normalized_needles):
            return _decoded(value)
    return None


def _decoded(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\xa0", " ").casefold()
    text = re.sub(r"[\u200e\u200f]", "", text)
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(_decoded(value) or ""))


def _object_id(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if hasattr(value, "pk"):
        value = value.pk
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
