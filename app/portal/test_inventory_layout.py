import uuid
from datetime import date
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from .inventory_models import InventoryIssue
from .inventory_services import save_issue
from .models import (ActivityLog, Course, CourseInstructor, CourseInstructorInventoryUsage,
    CourseSession, Instructor, InstructorInventoryBalance, InstructorRole, InventoryItem, Registration)


class InventoryTransactionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user("stock-admin", is_superuser=True, is_staff=True)
        self.user = User.objects.create_user("own-stock")
        self.instructor = Instructor.objects.create(user=self.user, name_english="FIRST INSTRUCTOR")
        self.peer = Instructor.objects.create(name_english="PEER INSTRUCTOR")
        self.item = InventoryItem.objects.create(name="Bandage", unit="each", quantity_on_hand=30)
        self.second = InventoryItem.objects.create(name="Training kit", quantity_on_hand=10)

    def issue(self, **changes):
        arguments = dict(actor=self.admin, instructor_id=self.instructor.pk,
            lines=[(self.item.pk, 8), (self.second.pk, 3)], issued_on="2026-09-01")
        arguments.update(changes)
        return save_issue(**arguments)

    def test_multi_line_issue_retry_edit_and_reversal_adjust_only_the_delta(self):
        token = uuid.uuid4()
        issue = self.issue(token=token)
        self.issue(token=token)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 22)
        self.assertEqual(InventoryIssue.objects.count(), 1)
        issue = self.issue(issue_id=issue.public_id, expected_revision=1,
            lines=[(self.item.pk, 6), (self.second.pk, 3)], issued_on="2026-09-02")
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 24)
        self.assertEqual(issue.issued_on, date(2026, 9, 2))
        self.assertEqual(InstructorInventoryBalance.objects.get(instructor=self.instructor, item=self.item).quantity_on_hand, 6)
        save_issue(actor=self.admin, issue_id=issue.public_id, expected_revision=2, reverse=True)
        self.item.refresh_from_db()
        issue.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 30)
        self.assertIsNotNone(issue.reversed_at)
        self.assertEqual(issue.lines.count(), 2)
        self.assertEqual(ActivityLog.objects.filter(object_type="InventoryIssue", object_id=str(issue.public_id)).count(), 3)

    def test_invalid_second_line_rolls_back_every_balance_and_audit(self):
        with self.assertRaises(ValidationError):
            self.issue(lines=[(self.item.pk, 8), (self.second.pk, 11)])
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 30)
        self.assertFalse(InstructorInventoryBalance.objects.exists())
        self.assertFalse(InventoryIssue.objects.exists())
        self.assertFalse(ActivityLog.objects.filter(object_type="InventoryIssue").exists())

    def test_used_stock_and_stale_edit_cannot_be_reversed(self):
        issue = self.issue()
        InstructorInventoryBalance.objects.filter(instructor=self.instructor, item=self.item).update(quantity_on_hand=2)
        with self.assertRaises(ValidationError):
            save_issue(actor=self.admin, issue_id=issue.public_id, expected_revision=1, reverse=True)
        with self.assertRaises(ValidationError):
            self.issue(issue_id=issue.public_id, expected_revision=0)
        issue.refresh_from_db()
        self.item.refresh_from_db()
        self.assertIsNone(issue.reversed_at)
        self.assertEqual(issue.revision, 1)
        self.assertEqual(self.item.quantity_on_hand, 22)

    def test_recipient_change_reverses_original_and_issues_to_new_holder(self):
        issue = self.issue()
        self.issue(issue_id=issue.public_id, expected_revision=1, instructor_id=self.peer.pk)
        self.assertEqual(InstructorInventoryBalance.objects.get(instructor=self.instructor, item=self.item).quantity_on_hand, 0)
        self.assertEqual(InstructorInventoryBalance.objects.get(instructor=self.peer, item=self.item).quantity_on_hand, 8)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, 22)

    def test_instructor_cannot_forge_warehouse_issue(self):
        with self.assertRaises(PermissionDenied):
            self.issue(actor=self.user)
        self.client.force_login(self.user)
        response = self.client.post(reverse("inventory"), {"action": "save_issue", "instructor_id": self.instructor.pk,
            "line_item": [self.item.pk], "line_quantity": [5]})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(InventoryIssue.objects.exists())


class CourseWorkspaceLayoutTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user("workspace-admin", is_superuser=True)
        self.user = User.objects.create_user("workspace-instructor")
        self.instructor = Instructor.objects.create(user=self.user, name_english="OWN MATERIAL")
        self.peer = Instructor.objects.create(name_english="PEER MATERIAL")
        self.course = Course.objects.create(title_english="Training")
        self.session = CourseSession.objects.create(course=self.course, status="registration_closed", start_date=date(2026,9,1))
        for instructor in (self.instructor, self.peer):
            CourseInstructor.objects.create(session=self.session, instructor=instructor)
        self.item = InventoryItem.objects.create(name="Own stock only")
        for instructor in (self.instructor, self.peer):
            InstructorInventoryBalance.objects.create(instructor=instructor, item=self.item, quantity_on_hand=10)
        self.client.force_login(self.user)

    def test_only_own_material_section_is_visible_and_peer_post_is_denied(self):
        # Lead-instructor course administration must not expose a colleague's
        # personal material report, even though that role can manage courses.
        self.instructor.roles.add(InstructorRole.objects.create(name="Lead Instructor"))
        response = self.client.get(reverse("instructor_course_workspace", args=[self.session.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["inventory_sections"]), 1)
        self.assertEqual(response.context["inventory_sections"][0]["assignment"].instructor_id, self.instructor.pk)
        response = self.client.post(reverse("course_inventory_save", args=[self.session.public_id]),
            {"instructor_id": self.peer.pk, f"used_up_{self.item.pk}": 4})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CourseInstructorInventoryUsage.objects.exists())

    def test_repeated_material_save_deducts_once(self):
        url = reverse("course_inventory_save", args=[self.session.public_id])
        data = {"instructor_id": self.instructor.pk, f"used_up_{self.item.pk}": 4}
        self.client.post(url, data)
        self.client.post(url, data)
        self.assertEqual(InstructorInventoryBalance.objects.get(instructor=self.instructor, item=self.item).quantity_on_hand, 6)
        self.client.post(url, {"instructor_id": self.instructor.pk})
        self.assertEqual(InstructorInventoryBalance.objects.get(instructor=self.instructor, item=self.item).quantity_on_hand, 6)

    def test_drafts_do_not_leak_into_my_courses_and_lifecycle_is_confirmed(self):
        draft = CourseSession.objects.create(course=self.course, status="draft")
        CourseInstructor.objects.create(session=draft, instructor=self.instructor)
        response = self.client.get(reverse("my_courses"))
        self.assertEqual([row["session"].pk for row in response.context["rows"]], [self.session.pk])
        self.assertEqual(response.context["rows"][0]["lifecycle_label"], "Confirmed")
        response = self.client.get(reverse("my_courses"), {"status": "draft"})
        self.assertEqual(response.context["rows"], [])

    def test_explicit_attendance_is_independent_and_rejection_removes_enrolment(self):
        registration = Registration.objects.create(requested_session=self.session,
            submitted_name_english="ATTENDING STUDENT", submitted_name_arabic="طالب",
            eid_raw="784199012345671", eid_normalized="784199012345671", email_raw="student@example.com")
        url = reverse("course_roster_save", args=[self.session.public_id])
        self.client.post(url, {"attendance_fields": "yes", f"s1_{registration.public_id}": "yes"})
        registration.refresh_from_db()
        self.assertTrue(registration.day1_attended)
        self.assertFalse(registration.day2_attended)
        self.assertEqual(registration.assessment_status, "pending")
        self.client.post(url, {"attendance_fields": "yes", f"selected_{registration.public_id}": "yes",
            f"rejected_{registration.public_id}": "yes"})
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.REJECTED)
        self.assertFalse(registration.selected_for_roster)

    def test_roster_typo_correction_preserves_master_identity_used_by_other_courses(self):
        from .models import Student, TrainingRecord
        student = Student.objects.create(name_english="ORIGINAL STUDENT", name_arabic="طالب",
            eid="784199012345671", email="old@example.com")
        prior = CourseSession.objects.create(course=self.course, start_date=date(2025, 1, 1))
        TrainingRecord.objects.create(student=student, session=prior, result="pass")
        registration = Registration.objects.create(student=student, requested_session=self.session,
            submitted_name_english="ORIGINAL STUDENT", submitted_name_arabic="طالب",
            eid_raw=student.eid, eid_normalized=student.eid, email_raw=student.email)
        key = registration.public_id
        response = self.client.post(reverse("course_roster_save", args=[self.session.public_id]), {
            f"name_english_{key}": "CORRECTED STUDENT", f"name_arabic_{key}": "طالب",
            f"email_{key}": "NEW@EXAMPLE.COM", f"eid_{key}": student.eid,
        })
        self.assertEqual(response.status_code, 302)
        registration.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(registration.submitted_name_english, "CORRECTED STUDENT")
        self.assertEqual(registration.email_raw, "new@example.com")
        self.assertEqual(student.name_english, "ORIGINAL STUDENT")
        self.assertEqual(student.training_records.get(session=prior).result, "pass")
