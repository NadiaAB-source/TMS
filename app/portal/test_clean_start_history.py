"""Integration checks run in Django's separate test database during setup."""
from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.db import transaction

from . import models as m
from .clean_start_service import reset_copy
from .course_services import enable_course_sequence_numbering
from .history_import_schema import SCHEMA, ImportProblem, read_upload
from .history_import_service import apply_import
from .access import feature_permissions


@override_settings(IQARUS_CHRONOLOGICAL_NUMBERING=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SECURE_SSL_REDIRECT=False)
class CleanStartImportTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('Administrator', 'admin@example.com', 'test-admin-secret')
        self.user = get_user_model().objects.create_user('trainer', 'trainer@example.com', 'test-trainer-secret')
        self.staff = m.Instructor.objects.create(name_english='Example Trainer', user=self.user, active=True)
        self.camp = m.Camp.objects.create(name='Example Camp')
        self.item = m.InventoryItem.objects.create(name='Example Item', category='consumable', unit='each', quantity_on_hand=90)
        self.balance = m.InstructorInventoryBalance.objects.create(instructor=self.staff, item=self.item, quantity_on_hand=10)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(BASE_DIR=Path(self.temp.name) / 'app')
        self.override.enable()
        self.addCleanup(self.override.disable)
        enable_course_sequence_numbering()
        self.client.force_login(self.admin)

    def package(self, name='A', start='2026-08-03'):
        rows = {key: [] for key in SCHEMA}
        def row(sheet, **values):
            values.update(_sheet=sheet, _row=2 + len(rows[sheet]))
            rows[sheet].append(values)
        row('Courses', course_key=name, course_code='TCCC', course_name='Test course', start_date=start,
            end_date=start, camp_name=self.camp.name, capacity=20, status='completed', registration_open='no')
        row('Students', student_key='S1', emirates_id='784-1990-0000001-0', name_english='EXAMPLE STUDENT',
            name_arabic='طالب مثال', email='student@example.com', identity_status='verified')
        row('Enrolments', course_key=name, student_key='S1', registration_status='approved', selected='yes',
            day1='yes', day2='yes', result='passed', hp='yes', ttt='no',
            duplicate_decision='allow', duplicate_notes='Original historical evidence checked.')
        row('Trainers', course_key=name, username='trainer', role='instructor')
        return dict(rows=rows, documents={}, sha256=(name.encode().hex()*64)[:64], filename=name+'.xlsx', size=100)

    def test_reset_preserves_credentials_and_names_but_clears_history_grants_and_equipment_allocations(self):
        apply_import(self.package(), self.admin, commit=True)
        group = Group.objects.create(name='Custom privilege')
        self.user.groups.add(group)
        m.StaffPermissionOverride.objects.create(user=self.user, feature='reports', level='edit')
        passwords = {u.pk: u.password for u in get_user_model().objects.all()}
        with self.captureOnCommitCallbacks(execute=True):
            result = reset_copy('Administrator')
        for model in (m.Student,m.Course,m.CourseSession,m.Registration,m.TrainingRecord,m.InstructorAllocation,m.J35GridCell,m.SourceFile):
            self.assertEqual(model.objects.count(), 0, model.__name__)
        self.assertEqual(m.Camp.objects.get().name, 'Example Camp')
        self.assertEqual(m.Instructor.objects.get().name_english, 'Example Trainer')
        self.admin.refresh_from_db();self.user.refresh_from_db();self.balance.refresh_from_db();self.item.refresh_from_db()
        self.assertTrue(self.admin.is_active and self.admin.is_staff and self.admin.is_superuser)
        self.assertTrue(self.user.is_active)
        self.assertFalse(self.user.is_staff or self.user.is_superuser)
        self.assertFalse(self.user.groups.filter(name='Custom privilege').exists())
        self.assertEqual(self.user.tms_permission_overrides.count(), 0)
        self.assertEqual(feature_permissions(self.user)['reports'], '')
        self.assertEqual(feature_permissions(self.user)['own_courses'], 'edit')
        self.assertEqual({u.pk:u.password for u in get_user_model().objects.all()}, passwords)
        self.assertEqual(self.balance.quantity_on_hand, 0)
        self.assertEqual(self.item.quantity_on_hand, 100)
        self.assertEqual(result['next_course_number'], 1)
        self.assertEqual(m.CourseSequenceCounter.objects.get().next_number, 1)

    def test_reset_command_refuses_unmarked_project(self):
        with self.assertRaises(CommandError):
            call_command('prepare_clean_start', admin='Administrator')
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_preview_writes_neither_database_nor_roster_files(self):
        summary = apply_import(self.package(), self.admin, commit=False)
        self.assertEqual(summary['courses'], 1)
        self.assertEqual(m.CourseSession.objects.count(), 0)
        self.assertEqual(m.Student.objects.count(), 0)
        self.assertFalse((Path(self.temp.name)/'saved course lists').exists())
        self.assertEqual(m.CourseSequenceCounter.objects.get().next_number, 1)

    def test_import_links_native_workflow_and_is_idempotent(self):
        p = self.package()
        summary = apply_import(p, self.admin, commit=True)
        session = m.CourseSession.objects.get(); reg = m.Registration.objects.get()
        self.assertEqual(summary['students_created'], 1)
        self.assertEqual(session.sequence_number, 1)
        self.assertEqual(m.TrainingRecord.objects.get().raw_payload['registration_public_id'], str(reg.public_id))
        self.assertEqual(m.CourseInstructor.objects.get().instructor, self.staff)
        self.assertEqual(m.InstructorAllocation.objects.get().session, session)
        self.assertEqual(m.J35GridCell.objects.get().course, session.course)
        snapshot = m.CourseRosterSnapshot.objects.get()
        self.assertTrue((Path(self.temp.name)/'saved course lists'/snapshot.stored_path).is_file())
        self.assertTrue(apply_import(p, self.admin, commit=True)['already_imported'])
        self.assertEqual(m.CourseSession.objects.count(), 1)
        self.assertEqual(m.Registration.objects.count(), 1)
        for route in ('instructor_course_workspace','registration_qr'):
            self.assertEqual(self.client.get(reverse(route,kwargs={'public_id':session.public_id})).status_code, 200,route)
        for route in ('my_courses','students','courses_sessions','reports','historical_import'):
            self.assertEqual(self.client.get(reverse(route)).status_code,200,route)
        # A normal Save changes must update the imported result, not create another.
        from .course_workflow_views import _save_course_roster
        rid=str(reg.public_id)
        request=RequestFactory().post('/',{f'selected_{rid}':'yes',f'result_{rid}':'failed',f'hp_{rid}':'no',
            f's1_{rid}':'yes',f's2_{rid}':'yes','attendance_fields':'yes',f'remarks_{rid}':'Edited on website'})
        request.user=self.admin
        with transaction.atomic():
            _save_course_roster(request, session)
        self.assertEqual(m.TrainingRecord.objects.count(),1)
        self.assertEqual(m.TrainingRecord.objects.get().result,'fail')

    def test_date_order_changes_numbers_but_never_public_links(self):
        apply_import(self.package('LATER','2026-09-01'),self.admin,commit=True)
        later=m.CourseSession.objects.get();link=(later.public_id,later.public_registration_token)
        apply_import(self.package('EARLIER','2026-08-01'),self.admin,commit=True)
        later.refresh_from_db()
        self.assertEqual(later.sequence_number,2)
        self.assertEqual((later.public_id,later.public_registration_token),link)
        self.assertEqual(m.Student.objects.count(),1)
        self.assertEqual(m.TrainingRecord.objects.count(),2)
        later.start_date=date(2026,7,1);later.end_date=later.start_date;later.save()
        self.assertEqual(later.sequence_number,1)
        older=m.CourseSession.objects.exclude(pk=later.pk).get();older.refresh_from_db()
        self.assertEqual(older.sequence_number,2)

    def test_bad_batch_rolls_back_all_records_and_stock(self):
        p=self.package();p['rows']['Enrolments'][0]['email']='ignored'
        p['rows']['Trainers'][0]['username']='missing-trainer'
        with self.assertRaises(ImportProblem):apply_import(p,self.admin,commit=True)
        self.assertFalse(m.CourseSession.objects.exists());self.assertFalse(m.Student.objects.exists())
        self.assertFalse(m.SourceFile.objects.exists())
        self.balance.refresh_from_db();self.assertEqual(self.balance.quantity_on_hand,10)

    def test_same_course_key_cannot_be_overwritten_by_changed_upload(self):
        p=self.package();apply_import(p,self.admin,commit=True);p['sha256']='f'*64
        with self.assertRaises(ImportProblem):apply_import(p,self.admin,commit=True)
        self.assertEqual(m.CourseSession.objects.count(),1)

    def test_identity_conflict_stops_import(self):
        apply_import(self.package(),self.admin,commit=True)
        p=self.package('B','2026-08-10');p['rows']['Students'][0]['name_english']='DIFFERENT PERSON'
        with self.assertRaises(ImportProblem):apply_import(p,self.admin,commit=True)
        self.assertEqual(m.CourseSession.objects.count(),1)

    def test_duplicate_requires_annotation(self):
        apply_import(self.package(),self.admin,commit=True)
        p=self.package('B','2026-08-10');p['rows']['Enrolments'][0]['duplicate_decision']=''
        with self.assertRaises(ImportProblem):apply_import(p,self.admin,commit=True)
        self.assertEqual(m.CourseSession.objects.count(),1)

    def test_trainer_overlap_requires_reason(self):
        apply_import(self.package(),self.admin,commit=True)
        with self.assertRaises(ImportProblem):apply_import(self.package('B'),self.admin,commit=True)
        p=self.package('B');p['rows']['Trainers'][0]['conflict_reason']='Split day, original schedules checked.'
        apply_import(p,self.admin,commit=True)
        self.assertEqual(m.InstructorAllocation.objects.count(),2)

    def test_stock_is_deducted_only_when_requested_and_only_once(self):
        p=self.package()
        p['rows']['Materials']=[dict(_sheet='Materials',_row=2,course_key='A',username='trainer',
            item_name=self.item.name,quantity_used=4,quantity_consumed=2,quantity_deteriorated=1,stock_treatment='deduct_now')]
        apply_import(p,self.admin,commit=True);apply_import(p,self.admin,commit=True)
        self.balance.refresh_from_db();self.assertEqual(self.balance.quantity_on_hand,7)
        self.assertEqual(m.InstructorInventoryMovement.objects.get().quantity_change,-3)
        p2=self.package('B','2026-08-10');p2['rows']['Materials']=deepcopy(p['rows']['Materials'])
        p2['rows']['Materials'][0].update(course_key='B',stock_treatment='already_in_balance')
        apply_import(p2,self.admin,commit=True)
        self.balance.refresh_from_db();self.assertEqual(self.balance.quantity_on_hand,7)

    def test_missing_scan_rolls_back_and_real_scan_is_linked(self):
        p=self.package();p['rows']['Documents']=[dict(_sheet='Documents',_row=2,course_key='A',file_name='stamped.pdf')]
        with self.assertRaises(ImportProblem):apply_import(p,self.admin,commit=True)
        self.assertFalse(m.CourseSession.objects.exists())
        p['documents']['stamped.pdf']=b'%PDF-1.4\n% test fixture\n%%EOF\n'
        apply_import(p,self.admin,commit=True)
        archive=m.StampedListArchive.objects.get()
        self.assertEqual((Path(self.temp.name)/'stamped lists'/archive.stored_path).read_bytes(),p['documents']['stamped.pdf'])

    def test_staff_cannot_use_admin_import(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('historical_import')).status_code,403)

    def test_shipped_example_is_readable_and_complete(self):
        path=Path(__file__).parent/'resources/Historical_Import_Example.xlsx'
        package=read_upload(path.read_bytes(),path.name)
        self.assertEqual(len(package['rows']['Courses']),2)
        self.assertEqual(len(package['rows']['Enrolments']),3)
