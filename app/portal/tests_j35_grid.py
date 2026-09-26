from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from .j35_grid_models import J35GridCell, J35GridCourseLink
from .j35_grid_services import clear_grid_cell, save_grid_cell
from .j35_grid_views import j35_grid_context
from .models import (
    ActivityLog, Camp, Course, CourseInstructor, CourseSession,
    Instructor, InstructorAllocation, Notification, Registration, Student,
)


class J35SpreadsheetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user("grid-admin", is_superuser=True, is_staff=True)
        cls.trainer_user = User.objects.create_user("grid-trainer")
        cls.other_user = User.objects.create_user("grid-trainer-two")
        cls.trainer = Instructor.objects.create(name_english="First Trainer", user=cls.trainer_user)
        cls.other = Instructor.objects.create(name_english="Second Trainer", user=cls.other_user)
        cls.camp = Camp.objects.create(name="Grid Camp")
        cls.course = Course.objects.create(title_english="Grid Course", code="GRID")

    def payload(self, **overrides):
        values = {
            "instructor": str(self.trainer.pk), "start_date": "2026-10-05",
            "end_date": "2026-10-06", "activity": self.camp.name,
            "camp": str(self.camp.pk), "course": str(self.course.pk),
            "kind": "course", "status": "draft", "color": "#c7efd0",
        }
        values.update(overrides)
        return values

    def save(self, **overrides):
        return save_grid_cell(user=self.admin, data=self.payload(**overrides))

    def edit(self, allocation, **overrides):
        cell = J35GridCell.objects.get(allocation=allocation)
        values = self.payload(
            allocation=str(allocation.pk), revision=str(cell.revision),
            status="confirmed" if allocation.status == InstructorAllocation.Status.PUBLISHED else "draft",
            session=str(allocation.session_id or ""),
        )
        values.update(overrides)
        return save_grid_cell(user=self.admin, data=values)

    def test_draft_never_creates_course_or_notification(self):
        allocation = self.save()
        self.assertIsNone(allocation.session_id)
        self.assertFalse(CourseSession.objects.exists())
        self.assertFalse(CourseInstructor.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_two_instructors_confirm_into_one_canonical_session(self):
        first = self.save(status="confirmed")
        second = self.save(instructor=str(self.other.pk), status="confirmed")
        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(CourseSession.objects.count(), 1)
        self.assertEqual(CourseInstructor.objects.count(), 2)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertTrue(J35GridCourseLink.objects.filter(session=first.session).exists())

    def test_repeated_save_does_not_repeat_confirmation_or_reset_progress(self):
        allocation = self.save(status="confirmed")
        session = allocation.session
        session.status = CourseSession.Status.IN_PROGRESS
        session.save()
        self.edit(allocation, color="#ffdce5")
        session.refresh_from_db()
        self.assertEqual(session.status, CourseSession.Status.IN_PROGRESS)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(CourseInstructor.objects.count(), 1)

    def test_new_instructor_cannot_be_added_to_progressed_course_from_grid(self):
        allocation = self.save(status="confirmed")
        allocation.session.status = CourseSession.Status.COMPLETED
        allocation.session.save()
        with self.assertRaisesMessage(ValidationError, "already has saved work"):
            self.save(instructor=str(self.other.pk), status="confirmed")
        self.assertEqual(CourseInstructor.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_changing_confirmed_task_text_notifies_the_instructor(self):
        allocation = self.save(status="confirmed", kind="other", activity="Office duty", camp="", course="")
        self.edit(allocation, kind="other", activity="Client meeting", camp="", course="")
        self.assertEqual(Notification.objects.count(), 2)
        self.assertIn("Client meeting", Notification.objects.latest("pk").message)

    def test_confirmed_cell_cannot_downgrade_after_student_registration(self):
        allocation = self.save(status="confirmed")
        student = Student.objects.create(name_english="Registered learner")
        Registration.objects.create(requested_session=allocation.session, student=student)
        with self.assertRaisesMessage(ValidationError, "already has saved work"):
            self.edit(allocation, status="draft", session="")
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, InstructorAllocation.Status.PUBLISHED)
        self.assertEqual(allocation.session.registrations.count(), 1)
        self.assertTrue(CourseInstructor.objects.filter(session=allocation.session, instructor=self.trainer).exists())

    def test_empty_grid_course_is_retained_as_draft_then_reused(self):
        allocation = self.save(status="confirmed")
        original_session = allocation.session
        draft = self.edit(allocation, status="draft", session="")
        original_session.refresh_from_db()
        self.assertIsNone(draft.session_id)
        self.assertEqual(original_session.status, CourseSession.Status.DRAFT)
        self.assertFalse(CourseInstructor.objects.exists())
        confirmed = self.edit(draft, status="confirmed")
        self.assertEqual(confirmed.session_id, original_session.pk)
        self.assertEqual(CourseSession.objects.count(), 1)

    def test_legacy_assignment_is_not_removed_when_editing_grid(self):
        session = CourseSession.objects.create(
            course=self.course, camp=self.camp, start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 6), status=CourseSession.Status.REGISTRATION_CLOSED,
        )
        CourseInstructor.objects.create(session=session, instructor=self.trainer)
        allocation = self.save(status="confirmed", session=str(session.pk))
        self.assertFalse(J35GridCell.objects.get(allocation=allocation).owns_assignment)
        with self.assertRaisesMessage(ValidationError, "outside this grid"):
            self.edit(allocation, status="draft", session="")
        self.assertTrue(CourseInstructor.objects.filter(session=session, instructor=self.trainer).exists())

    def test_conflict_rolls_back_all_changes_unless_reason_recorded(self):
        InstructorAllocation.objects.create(
            instructor=self.trainer, activity="Leave", start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 6), allocation_kind="leave", status="published",
        )
        with self.assertRaisesMessage(ValidationError, "overlapping work"):
            self.save(status="confirmed")
        self.assertFalse(CourseSession.objects.exists())
        self.assertEqual(InstructorAllocation.objects.count(), 1)
        allocation = self.save(status="confirmed", override_reason="Leave cancelled by client request; reviewed by lead.")
        self.assertTrue(allocation.session_id)
        self.assertIn("Leave cancelled", ActivityLog.objects.latest("pk").details["override_reason"])

    def test_canonical_booking_conflict_is_detected_even_without_old_grid_row(self):
        other_camp = Camp.objects.create(name="Other Camp")
        session = CourseSession.objects.create(
            course=self.course, camp=other_camp, start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 6), status=CourseSession.Status.REGISTRATION_OPEN,
        )
        CourseInstructor.objects.create(session=session, instructor=self.trainer)
        with self.assertRaisesMessage(ValidationError, "overlapping work"):
            self.save(status="confirmed")
        self.assertEqual(CourseSession.objects.count(), 1)

    def test_stale_revision_cannot_overwrite_a_newer_edit(self):
        allocation = self.save()
        self.edit(allocation, color="#eeeeee")
        with self.assertRaisesMessage(ValidationError, "another window"):
            self.edit(allocation, revision="1", color="#000000")
        self.assertEqual(J35GridCell.objects.get(allocation=allocation).color, "#eeeeee")

    def test_nonplanner_cannot_view_or_write_grid(self):
        request = RequestFactory().get("/")
        request.user = self.trainer_user
        self.assertEqual(j35_grid_context(request), {"j35_grid_visible": False})
        with self.assertRaises(PermissionDenied):
            save_grid_cell(user=self.trainer_user, data=self.payload(status="confirmed"))

    def test_grid_displays_all_active_instructors_and_exactly_fourteen_days(self):
        request = RequestFactory().get("/?j35_start=2026-10-05&course=12")
        request.user = self.admin
        context = j35_grid_context(request)
        self.assertEqual(len(context["j35_grid_days"]), 14)
        self.assertEqual(len(context["j35_grid_rows"]), 2)
        self.assertIn("course=12", context["j35_grid_next"])
        self.assertEqual(context["j35_grid_end"], date(2026, 10, 18))

    def test_existing_courses_are_visible_without_creating_or_duplicating_allocations(self):
        session = CourseSession.objects.create(
            course=self.course, camp=self.camp, start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 6), status=CourseSession.Status.REGISTRATION_CLOSED,
        )
        CourseInstructor.objects.create(session=session, instructor=self.trainer)
        request = RequestFactory().get("/?j35_start=2026-10-05")
        request.user = self.admin
        context = j35_grid_context(request)
        row = next(row for row in context["j35_grid_rows"] if row["instructor"].pk == self.trainer.pk)
        self.assertTrue(row["days"][0]["cells"][0]["read_only"])
        self.assertFalse(InstructorAllocation.objects.exists())
        self.save(status="confirmed", session=str(session.pk))
        context = j35_grid_context(request)
        row = next(row for row in context["j35_grid_rows"] if row["instructor"].pk == self.trainer.pk)
        self.assertEqual(len(row["days"][0]["cells"]), 1)

    def test_assignment_cannot_be_edited_using_another_instructor_id(self):
        allocation = self.save()
        with self.assertRaisesMessage(ValidationError, "unavailable"):
            self.edit(allocation, instructor=str(self.other.pk))

    def test_confirm_requires_real_course_and_camp_and_rejects_unsafe_colour(self):
        with self.assertRaises(ValidationError):
            self.save(status="confirmed", course="")
        with self.assertRaises(ValidationError):
            self.save(status="confirmed", camp="", activity="Unrecognised camp")
        with self.assertRaises(ValidationError):
            self.save(color="red;position:fixed")
        self.assertFalse(CourseSession.objects.exists())

    def test_ajax_endpoint_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(reverse("j35_grid_save"), self.payload())
        self.assertEqual(response.status_code, 403)

    def test_clear_draft_retains_history_and_cannot_clear_confirmed_work(self):
        draft = self.save()
        data = {"allocation": draft.pk, "instructor": self.trainer.pk, "revision": 1}
        clear_grid_cell(user=self.admin, data=data)
        draft.refresh_from_db()
        self.assertEqual(draft.status, InstructorAllocation.Status.CANCELLED)
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.CANCEL).exists())
        confirmed = self.save(status="confirmed")
        data.update(allocation=confirmed.pk)
        with self.assertRaises(ValidationError):
            clear_grid_cell(user=self.admin, data=data)
        self.assertEqual(CourseInstructor.objects.count(), 1)
