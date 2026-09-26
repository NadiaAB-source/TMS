from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .directory_services import filtered_student_rows, session_totals
from .models import Course, CourseSession, Instructor, Registration, StampedListArchive, Student, TrainingRecord


class DirectoryReportLayoutTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="directory-admin", is_staff=True, is_superuser=True)
        self.user = get_user_model().objects.create_user(username="directory-instructor")
        self.instructor = Instructor.objects.create(user=self.user, name_english="Directory Instructor")
        self.course = Course.objects.create(title_english="Directory Test Course")
        self.other_course = Course.objects.create(title_english="Other Course")
        self.first = CourseSession.objects.create(course=self.course, start_date=date(2026, 1, 5), end_date=date(2026, 1, 6), status="completed")
        self.second = CourseSession.objects.create(course=self.other_course, start_date=date(2026, 9, 20), end_date=date(2026, 9, 21), status="registration_open", registration_published=True)
        self.student = Student.objects.create(name_english="Student With Two Courses", name_arabic="طالب في دورتين", eid="784199012345671", identity_status="verified")
        self.record = TrainingRecord.objects.create(student=self.student, session=self.first, result="pass", is_hp=True)
        self.registration = Registration.objects.create(student=self.student, requested_session=self.first, selected_for_roster=True, assessment_status="passed", is_hp=True)
        TrainingRecord.objects.create(student=self.student, session=self.second, result="fail")

    def test_directory_keeps_two_course_outcomes_without_double_counting_registration(self):
        rows = filtered_student_rows({"active": "yes"})
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["outcome"] for row in rows}, {"pass", "fail"})
        matched = filtered_student_rows({"course": str(self.course.pk), "outcome": "pass", "identity_status": "verified"})
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["session"], self.first)
        self.assertEqual(filtered_student_rows({"course": str(self.course.pk), "outcome": "fail"}), [])

    def test_student_without_course_remains_visible_and_date_filter_uses_overlap(self):
        unassigned = Student.objects.create(name_english="No Course Yet")
        self.assertIn(unassigned.pk, [row["student"].pk for row in filtered_student_rows({})])
        rows = filtered_student_rows({"date_from": "2026-01-06", "date_to": "2026-01-06"})
        self.assertEqual([row["session"].pk for row in rows], [self.first.pk])
        self.assertEqual(len(filtered_student_rows({"date_from": "invalid"})), 3)

    def test_print_all_reconstructs_filters_across_pages_and_deduplicates_identity(self):
        for index in range(27):
            Student.objects.create(name_english=f"Matching Student {index:02}")
        excluded = Student.objects.create(name_english="Outside Filter")
        self.client.force_login(self.admin)
        response = self.client.post(reverse("students_print"), {"mode": "all_filtered", "filters": "q=Matching&page_size=25"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["students"]), 27)
        response = self.client.post(reverse("students_print"), {"mode": "selected", "filters": "q=Matching", "student_ids": [excluded.pk]})
        self.assertEqual(list(response.context["students"]), [])
        response = self.client.post(reverse("students_print"), {"mode": "all_filtered", "filters": "q=Student+With+Two+Courses"})
        self.assertEqual(len(response.context["students"]), 1)

    def test_enrollment_totals_are_canonical_and_number_filter_is_exact(self):
        values = session_totals([self.first, self.second])
        self.assertEqual(values[self.first.pk]["enrolled"], 1)
        self.assertEqual(values[self.first.pk]["passed"], 1)
        self.assertEqual(values[self.second.pk]["failed"], 1)
        self.client.force_login(self.admin)
        cards = self.client.get(reverse("my_courses"))
        card = next(row for row in cards.context["rows"] if row["session"].pk == self.second.pk)
        self.assertEqual(card["selected_count"], values[self.second.pk]["enrolled"])
        self.assertEqual(card["failed_count"], values[self.second.pk]["failed"])
        response = self.client.get(reverse("courses_sessions"), {"passed": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["public_id"] for row in response.context["rows"]], [self.first.public_id])
        response = self.client.get(reverse("courses_sessions"), {"status": "confirmed", "registration": "open"})
        self.assertEqual([row["public_id"] for row in response.context["rows"]], [self.second.public_id])

    def test_invalid_training_falls_back_to_selected_registration(self):
        self.record.duplicate_flag = True
        self.record.save(update_fields=["duplicate_flag"])
        values = session_totals([self.first])[self.first.pk]
        self.assertEqual(values["enrolled"], 1)
        self.assertEqual(values["passed"], 1)

    def test_source_registration_link_prevents_an_unlinked_registration_being_counted_twice(self):
        self.registration.student = None
        self.registration.save(update_fields=["student"])
        self.record.raw_payload = {"registration_public_id": str(self.registration.public_id)}
        self.record.save(update_fields=["raw_payload"])
        self.assertEqual(session_totals([self.first])[self.first.pk]["enrolled"], 1)

    def test_rejected_submission_does_not_overwrite_enrolled_course_participation(self):
        Registration.objects.create(student=self.student, requested_session=self.first, selected_for_roster=False, status="rejected")
        values = session_totals([self.first])[self.first.pk]
        self.assertEqual(values["enrolled"], 1)
        self.assertEqual(values["rejected"], 1)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("reports"), {"report": "student_rejected", "course": self.course.pk})
        self.assertEqual(len(response.context["student_rows"]), 1)

    def test_regular_instructor_can_view_students_but_reports_directory_are_forbidden(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("students")).status_code, 200)
        self.assertEqual(self.client.get(reverse("student_detail", args=[self.student.public_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("courses_sessions")).status_code, 403)
        self.assertEqual(self.client.get(reverse("reports")).status_code, 403)

    def test_report_filter_is_shared_and_only_chosen_outcome_is_present(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("reports"), {"report": "student_fail", "date_from": "2026-09-01", "date_to": "2026-09-30"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["student_rows"]), 1)
        self.assertEqual(response.context["student_rows"][0]["session"], self.second)
        self.assertContains(response, "report=course_completed")
        self.assertContains(response, "date_from=2026-09-01")
        response = self.client.get(reverse("reports"), {"report": "student_fail", "course": self.course.pk})
        self.assertEqual(response.context["student_rows"], [])

    def test_document_report_tracks_upload_actor_and_waiting_states(self):
        StampedListArchive.objects.create(session=self.first, original_name="stamped.pdf", stored_path="test/report-stamped.pdf", content_type="application/pdf", size_bytes=20, sha256="a" * 64, uploaded_by=self.admin)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("reports"), {"report": "documents_stamped", "document_state": "uploaded"})
        self.assertEqual([session.pk for session in response.context["sessions"]], [self.first.pk])
        self.assertContains(response, self.admin.username)
        response = self.client.get(reverse("reports"), {"report": "documents_frontend", "document_state": "waiting"})
        self.assertEqual(len(response.context["sessions"]), 2)
        self.assertContains(response, "Not generated")
