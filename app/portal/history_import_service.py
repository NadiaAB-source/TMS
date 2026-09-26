"""All-or-nothing import into the normal course, roster, J35 and stock models."""
from datetime import datetime, time
from io import BytesIO
from pathlib import Path
import hashlib
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from . import models as m
from .access import is_operations_admin, can_edit_reports, can_edit_all_courses
from .course_services import allocate_course_sequence_number, build_stamped_roster_workbook
from .duplicate_services import build_registration_review_rows
from .identity import format_emirates_id
from .student_services import eid_details, validated_registration_values
from .history_import_schema import (ImportProblem, text, fail, boolean, integer, choice,
    date_value, timestamp, key, serializable)


def permitted(actor):
    return bool(actor.is_active and is_operations_admin(actor) and can_edit_reports(actor) and can_edit_all_courses(actor))


def checked_save(obj, row):
    try:
        obj.full_clean()
        obj.save()
    except ValidationError as exc:
        fail(row, '; '.join(exc.messages))
    return obj


def unique_rows(rows, fields):
    seen = set()
    for row in rows:
        value = tuple(text(row.get(f)) for f in fields)
        if value in seen:
            fail(row, 'Repeated ' + ' + '.join(fields) + '. Keep one row per record.')
        seen.add(value)


def existing(model, field, value, row):
    matches = list(model.objects.filter(**{field + '__iexact': text(value)})[:2])
    if len(matches) != 1:
        fail(row, f'Cannot uniquely match {field}: {value}. Use the names/accounts listed on the import page.')
    return matches[0]


def account(name, actor, row):
    return existing(get_user_model(), 'username', name, row) if text(name) else actor


def backdate(obj, values):
    values = {k: v for k, v in values.items() if v is not None}
    if values:
        type(obj).objects.filter(pk=obj.pk).update(**values)
        for k, v in values.items():
            setattr(obj, k, v)


def evidence(source, row, **links):
    m.SourceRecord.objects.create(source_record_id=uuid.uuid4(), source_file=source,
        worksheet_name=row['_sheet'], source_row_number=row['_row'], record_type='native_history',
        raw_payload=serializable(row), import_status='converted', **links)


def apply_import(package, actor, *, commit=False):
    if not permitted(actor):
        raise PermissionDenied('Administrator access to all courses and reports is required.')
    rows = package['rows']
    created_files = []
    try:
        with transaction.atomic():
            m.CourseSequenceCounter.objects.get_or_create(key='course_session')
            counter = m.CourseSequenceCounter.objects.select_for_update().get(key='course_session')
            if not counter.enabled:
                raise ImportProblem('Course numbering is not enabled. Use the clean-start website for this import.')
            previous = m.SourceFile.objects.filter(sha256=package['sha256'], imported=True, notes__startswith='IQARUS-HISTORY-1').first()
            if previous:
                return {'already_imported': True, 'courses': 0, 'students_created': 0, 'registrations': 0,
                        'message': 'This exact file was already imported. No duplicate records were created.'}
            source = m.SourceFile.objects.create(filename=package['filename'][:255], sha256=package['sha256'],
                size_bytes=package['size'], imported=False, notes='IQARUS-HISTORY-1; normal workflow records')
            for sheet, fields in {'Courses': ('course_key',), 'Students': ('student_key',),
                'Enrolments': ('course_key', 'student_key'), 'Trainers': ('course_key', 'username'),
                'CourseHistory': ('course_key',), 'Materials': ('course_key', 'username', 'item_name'),
                'Documents': ('file_name',)}.items():
                unique_rows(rows[sheet], fields)
            courses, histories, students, staff, assignments = {}, {}, {}, {}, {}
            student_rows = {}
            new_students, registrations, registration_rows, document_writes = set(), [], {}, []
            for row in rows['CourseHistory']:
                histories[key(row, 'course_key')] = row
            # Import dates drive visible numbers; normal UUID links remain stable.
            sorted_courses = sorted(rows['Courses'], key=lambda row: (date_value(row, 'start_date'), key(row, 'course_key')))
            for row in sorted_courses:
                course_key = key(row, 'course_key')
                reference = 'HIST-' + course_key
                if m.CourseSession.objects.filter(reference_code=reference).exists():
                    fail(row, f'{course_key} is already in the website. Existing courses are not overwritten.')
                camp = existing(m.Camp, 'name', row['camp_name'], row)
                code = text(row['course_code'])
                existing_courses = list(m.Course.objects.filter(code__iexact=code)[:2])
                if len(existing_courses) > 1:
                    fail(row, 'Course code is ambiguous in the website.')
                if existing_courses:
                    course = existing_courses[0]
                    if text(course.title_english).casefold() != text(row['course_name']).casefold():
                        fail(row, 'Course code already belongs to a different course name.')
                else:
                    course = checked_save(m.Course(code=code, title_english=text(row['course_name'])), row)
                start, end = date_value(row, 'start_date'), date_value(row, 'end_date')
                if end < start:
                    fail(row, 'end_date is before start_date.')
                status = choice(row, 'status', ('draft', 'confirmed', 'in_progress', 'completed', 'cancelled'))
                registration_open = boolean(row, 'registration_open')
                if status in ('draft', 'completed', 'cancelled') and registration_open:
                    fail(row, 'Registration must be closed for a draft, completed or cancelled course.')
                history = histories.get(course_key, {'_sheet': 'CourseHistory', '_row': '?'} )
                branch = text(row.get('service_branch')).upper()
                if branch and branch not in dict(m.CourseSession.ServiceBranch.choices):
                    fail(row, 'service_branch must be LF, PG, AF, JA, NAVY or OTHER.')
                if branch == 'OTHER' and not text(row.get('service_branch_other')):
                    fail(row, 'Name the other service branch.')
                saved_status = ('registration_open' if registration_open else 'registration_closed') if status == 'confirmed' else status
                confirmed = timestamp(history, 'confirmed_at')
                if status == 'draft' and confirmed:
                    fail(history, 'A draft cannot have a confirmed_at value.')
                generated, uploaded = timestamp(history, 'frontend_generated_at'), timestamp(history, 'frontend_confirmed_at')
                if generated and uploaded and uploaded < generated:
                    fail(history, 'Front-End confirmation cannot precede generation.')
                if uploaded and (status != 'completed' or registration_open):
                    fail(history, 'A confirmed Front-End upload requires a completed course with registration closed.')
                session = checked_save(m.CourseSession(course=course, reference_code=reference,
                    sequence_number=allocate_course_sequence_number(), camp=camp, start_date=start, end_date=end,
                    capacity=integer(row, 'capacity', 1, 500), status=saved_status,
                    registration_published=registration_open,
                    instructor_student_ratio=integer(row, 'students_per_instructor', 1, 100, default=10),
                    service_branch=branch, service_branch_other=text(row.get('service_branch_other')),
                    poc_name=text(row.get('poc_name')), poc_contact_number=text(row.get('poc_phone')),
                    poc_location_url=text(row.get('map_url')), created_by=account(history.get('created_by'), actor, history),
                    confirmed_at=confirmed, course_information_sent_at=timestamp(history, 'information_sent_at'),
                    external_upload_generated_at=generated, external_upload_confirmed_at=uploaded,
                    external_upload_confirmed_by=account(history.get('frontend_confirmed_by'), actor, history) if uploaded else None,
                    notes=text(row.get('notes'))), row)
                backdate(session, {'created_at': timestamp(history, 'created_at')})
                courses[course_key] = session
                evidence(source, row, linked_course=course, linked_session=session, linked_camp=camp)
                if course_key in histories:
                    evidence(source, history, linked_course=course, linked_session=session)
            if set(histories) - set(courses):
                raise ImportProblem('CourseHistory contains a course_key missing from Courses.')
            for sheet in ('Enrolments', 'Trainers', 'Materials', 'Documents'):
                for row in rows[sheet]:
                    if key(row, 'course_key') not in courses:
                        fail(row, 'course_key is missing from Courses.')
            for row in rows['Students']:
                student_key = key(row, 'student_key')
                identity = choice(row, 'identity_status', ('verified', 'review', 'invalid_id'))
                raw_eid = text(row.get('emirates_id'))
                try:
                    digits, formatted = eid_details(raw_eid)
                except ValidationError:
                    if identity == 'verified':
                        fail(row, 'A verified student needs a valid Emirates ID.')
                    digits, formatted = '', ''
                matches = list(m.Student.objects.filter(eid__in=[digits, formatted]).exclude(eid__in=['', None])) if digits else []
                if len(matches) > 1:
                    fail(row, 'Multiple existing students match this Emirates ID. Resolve them before import.')
                english, arabic, email = text(row['name_english']).upper(), text(row.get('name_arabic')), text(row.get('email')).lower()
                if matches:
                    student = matches[0]
                    if student.name_english.strip().casefold() != english.casefold() or (arabic and student.name_arabic and arabic != student.name_arabic.strip()) or (email and student.email and email != student.email.lower()):
                        fail(row, 'Emirates ID matches an existing student with different details. Resolve the identity before import.')
                else:
                    student = checked_save(m.Student(eid=digits or None, name_english=english, name_arabic=arabic,
                        email=email, phone=text(row.get('phone')), identity_status=identity), row)
                    new_students.add(student.pk)
                if student.pk in {s.pk for s in students.values()}:
                    fail(row, 'Use the same student_key for the same person; do not repeat their Emirates ID in Students.')
                students[student_key] = student
                student_rows[student_key] = row
                evidence(source, row, linked_student=student)
            for row in rows['Trainers']:
                session = courses[key(row, 'course_key')]
                user = existing(get_user_model(), 'username', row['username'], row)
                try:
                    instructor = m.Instructor.objects.get(user=user)
                except m.Instructor.DoesNotExist:
                    fail(row, 'This username has no staff profile.')
                staff[user.username.casefold()] = instructor
                role = choice(row, 'role', ('instructor', 'observer', 'coordinator'))
                assignment = checked_save(m.CourseInstructor(session=session, instructor=instructor, assignment_role=role,
                    notified_at=timestamp(row, 'notified_at'), acknowledged_at=timestamp(row, 'acknowledged_at')), row)
                assignments[(session.pk, user.username.casefold())] = instructor
                color = text(row.get('j35_color')) or '#ffffff'
                allocation = m.InstructorAllocation(instructor=instructor, session=session, camp=session.camp,
                    activity=session.course.title_english[:200], start_date=session.start_date, end_date=session.end_date,
                    allocation_kind='course', status='cancelled' if session.status == 'cancelled' else 'draft' if session.status == 'draft' else 'published',
                    override_reason=text(row.get('conflict_reason')), created_by=actor, updated_by=actor,
                    notified_at=assignment.notified_at, notes='Imported historical course assignment.')
                checked_save(allocation, row)
                checked_save(m.J35GridCell(allocation=allocation, course=session.course, color=color, owns_assignment=True), row)
                evidence(source, row, linked_session=session, linked_instructor=instructor)
            for course_key, session in courses.items():
                if not session.instructor_assignments.exists():
                    raise ImportProblem(f'Courses: {course_key} needs at least one Trainers row.')
                # Mark as a normal editable grid-linked course so J35 can open its job card.
                m.J35GridCourseLink.objects.create(session=session, created_by=actor)
            first_new_registration = set()
            for row in rows['Enrolments']:
                student_key = key(row, 'student_key')
                if student_key not in students:
                    fail(row, 'student_key is missing from Students.')
                student, session = students[student_key], courses[key(row, 'course_key')]
                reg_status = choice(row, 'registration_status', ('pending', 'approved', 'rejected', 'needs_review'))
                selected = boolean(row, 'selected')
                result = choice(row, 'result', ('pending', 'passed', 'failed'))
                day1, day2, hp, ttt = (boolean(row, field) for field in ('day1', 'day2', 'hp', 'ttt'))
                if selected and reg_status != 'approved':
                    fail(row, 'A selected student must have registration_status approved.')
                if (result != 'pending' or day1 or day2 or hp or ttt) and not selected:
                    fail(row, 'Attendance/results require selected=yes and approved registration.')
                if result == 'passed' and not day1:
                    fail(row, 'A passed result requires day1 attendance.')
                reg = m.Registration(requested_session=session, student=student,
                    submitted_name_english=student.name_english, submitted_name_arabic=student.name_arabic,
                    eid_raw=text(student_rows[student_key].get('emirates_id')),
                    eid_normalized=student.eid or '', email_raw=student.email, phone_raw=student.phone,
                    submitted_unit=text(row.get('unit')), submitted_at=timestamp(row, 'submitted_at') or timezone.make_aware(datetime.combine(session.start_date, time.min)),
                    status=reg_status, selected_for_roster=selected, day1_attended=day1, day2_attended=day2,
                    is_hp=hp, is_ttt=ttt, assessment_status=result, instructor_remarks=text(row.get('remarks')),
                    source_file=source, source_sheet='Enrolments', source_row=row['_row'], raw_payload=serializable(row))
                if selected:
                    try:
                        validated_registration_values(reg)
                    except ValidationError as exc:
                        fail(row, '; '.join(exc.messages))
                checked_save(reg, row)
                if student.pk in new_students and student.pk not in first_new_registration:
                    backdate(reg, {'created_at': student.created_at})
                    first_new_registration.add(student.pk)
                registrations.append(reg)
                registration_rows[reg.pk] = row
                record = None
                if selected and (result != 'pending' or hp or ttt):
                    # Same registration_public_id link as Save changes in the course workspace.
                    record = checked_save(m.TrainingRecord(student=student, session=session,
                        attendance='present' if result in ('passed', 'failed') else 'partial' if day1 or day2 else 'pending',
                        result={'passed': 'pass', 'failed': 'fail'}.get(result, 'pending'),
                        record_status='completed' if result in ('passed', 'failed') else 'registered',
                        is_hp=hp, is_ttt=ttt, notes=reg.instructor_remarks, source_file=source,
                        source_sheet='IQARUS course workflow', source_row=row['_row'],
                        raw_payload={'registration_public_id': str(reg.public_id), 'historical_import': source.pk}), row)
                evidence(source, row, linked_session=session, linked_student=student,
                         linked_registration=reg, linked_training_record=record)
            used_students = {key(row, 'student_key') for row in rows['Enrolments']}
            if set(students) - used_students:
                raise ImportProblem('Students contains people with no Enrolments row. Add their course link or remove those rows.')
            # Run the website's real duplicate review after every history record exists.
            for review in build_registration_review_rows(registrations):
                reg = review['registration']
                row = registration_rows[reg.pk]
                decision = choice(row, 'duplicate_decision', ('', 'allow', 'exclude'), default='')
                note = text(row.get('duplicate_notes'))
                if review['review_required']:
                    if decision == 'allow' and note:
                        reg.duplicate_review_status = 'reviewed_allow'
                    elif decision == 'exclude' and note and not reg.selected_for_roster:
                        reg.duplicate_review_status = 'reviewed_exclude'
                    elif not reg.selected_for_roster and not decision:
                        reg.duplicate_review_status = 'review_required'
                    else:
                        fail(row, 'Duplicate review needed: ' + '; '.join(review['warnings']) + ' Set duplicate_decision=allow and explain why in duplicate_notes, or leave the student unselected for review.')
                    if reg.duplicate_review_status in ('reviewed_allow', 'reviewed_exclude'):
                        reg.duplicate_review_fingerprint = review['review_fingerprint']
                        reg.duplicate_review_notes = note
                        reg.duplicate_reviewed_by = actor
                        reg.duplicate_reviewed_at = timezone.now()
                    reg.save()
            from .chronological_numbering import renumber_courses
            numbers = renumber_courses()
            for course_key, session in courses.items():
                if session.pk in numbers:
                    session.sequence_number = numbers[session.pk]
                selected_regs = [r for r in registrations if r.requested_session_id == session.pk and r.selected_for_roster]
                if len(selected_regs) > session.capacity:
                    raise ImportProblem(f'{course_key}: selected students exceed course capacity.')
                if not selected_regs:
                    continue
                data, sha = build_stamped_roster_workbook(session, selected_regs)
                relative = f'{session.public_id}/roster-v1-{uuid.uuid4().hex}.xlsx'
                snapshot = m.CourseRosterSnapshot.objects.create(session=session, version=1, stored_path=relative,
                    sha256=sha, student_count=len(selected_regs), created_by=actor)
                history = histories.get(course_key, {})
                backdate(snapshot, {'created_at': timestamp(history, 'day1_list_created_at')})
                m.CourseRosterSnapshotItem.objects.bulk_create([m.CourseRosterSnapshotItem(snapshot=snapshot,
                    registration=r, serial_number=i, email=r.email_raw, eid=format_emirates_id(r.eid_normalized),
                    name_english=r.submitted_name_english, name_arabic=r.submitted_name_arabic)
                    for i, r in enumerate(selected_regs, 1)])
                document_writes.append((Path(settings.BASE_DIR).parent / 'saved course lists' / relative, data))
            stock_changes = 0
            for row in sorted(rows['Materials'], key=lambda r: courses[key(r, 'course_key')].start_date):
                session = courses[key(row, 'course_key')]
                instructor = assignments.get((session.pk, text(row['username']).casefold()))
                if not instructor:
                    fail(row, 'The trainer must be assigned to this course in Trainers.')
                item = existing(m.InventoryItem, 'name', row['item_name'], row)
                used, consumed, deteriorated = (integer(row, field) for field in ('quantity_used', 'quantity_consumed', 'quantity_deteriorated'))
                loss = consumed + deteriorated
                if loss > used:
                    fail(row, 'Consumed plus deteriorated cannot exceed quantity_used.')
                treatment = choice(row, 'stock_treatment', ('already_in_balance', 'deduct_now'))
                balance = m.InstructorInventoryBalance.objects.select_for_update().filter(instructor=instructor, item=item).first()
                if balance is None:
                    fail(row, 'This item has no trainer balance. Set up the correct equipment opening balances first.')
                if treatment == 'deduct_now':
                    if used > balance.quantity_on_hand:
                        fail(row, 'Used quantity exceeds the trainer balance.')
                    balance.quantity_on_hand -= loss
                    balance.updated_by = actor
                    balance.save(update_fields=['quantity_on_hand', 'updated_by', 'updated_at'])
                    stock_changes += loss
                    if loss:
                        movement = m.InstructorInventoryMovement.objects.create(instructor=instructor, item=item, session=session,
                            movement_type='course_report', quantity_change=-loss, balance_after=balance.quantity_on_hand,
                            recorded_by=actor, notes='Historical import: stock deducted once.')
                        # This is a present-day adjustment, not a fabricated backdated issue.
                usage = checked_save(m.CourseInstructorInventoryUsage(session=session, instructor=instructor, item=item,
                    quantity_used=used, quantity_consumed=consumed, quantity_deteriorated=deteriorated,
                    recorded_by=actor, notes=text(row.get('notes'))), row)
                evidence(source, row, linked_session=session, linked_instructor=instructor)
            listed_documents = set()
            for row in sorted(rows['Documents'], key=lambda r: timestamp(r, 'uploaded_at') or timezone.now()):
                name = text(row['file_name'])
                content = package['documents'].get(name)
                if content is None:
                    fail(row, f'Add documents/{name} to the ZIP.')
                from .stamped_list_views import _detected_file_type
                extension, mime = _detected_file_type(BytesIO(content))
                if not extension:
                    fail(row, 'Stamped lists must be PDF, JPG or PNG.')
                listed_documents.add(name)
                session = courses[key(row, 'course_key')]
                relative = f'{session.public_id}/{uuid.uuid4().hex}{extension}'
                m.StampedListArchive.objects.filter(session=session, active=True).update(active=False)
                archive = checked_save(m.StampedListArchive(session=session, original_name=name, stored_path=relative,
                    content_type=mime, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                    uploaded_by=account(row.get('uploaded_by'), actor, row), active=True, notes=text(row.get('notes'))), row)
                backdate(archive, {'uploaded_at': timestamp(row, 'uploaded_at')})
                document_writes.append((Path(settings.BASE_DIR).parent / 'stamped lists' / relative, content))
                evidence(source, row, linked_session=session)
            if set(package['documents']) != listed_documents:
                raise ImportProblem('Every scan in documents/ must have a matching Documents row.')
            from .chronological_numbering import renumber_courses
            numbers = renumber_courses()
            for session in courses.values():
                if session.pk in numbers:
                    session.sequence_number = numbers[session.pk]
            summary = {'already_imported': False, 'courses': len(courses), 'students_created': len(new_students),
                'students_matched': len(students) - len(new_students), 'registrations': len(registrations),
                'trainers': len(rows['Trainers']), 'material_rows': len(rows['Materials']),
                'units_deducted_now': stock_changes, 'stamped_lists': len(rows['Documents']),
                'day1_lists': len(document_writes) - len(rows['Documents']),
                'numbers': [s.sequence_number for s in courses.values()],
                'message': 'Historical records use the normal course workflow. No emails or trainer notifications were sent.'}
            source.imported, source.records_found = True, sum(len(r) for r in rows.values())
            source.save(update_fields=['imported', 'records_found', 'updated_at'])
            m.ActivityLog.objects.create(actor=actor, action='import', object_type='HistoricalImport', object_id=str(source.pk),
                description='Historical courses imported into the normal workflow.', details=summary)
            if commit:
                for target, content in document_writes:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as stream:
                        created_files.append(target)
                        stream.write(content)
            else:
                transaction.set_rollback(True)
            return summary
    except BaseException:
        for path in created_files:
            path.unlink(missing_ok=True)
        raise
