"""Permission boundaries for the staff matrix and delegated module access."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.test import Client, TestCase
from django.urls import reverse

from .access import (
    can_access_session, can_create_course, can_edit_session, can_edit_staff,
    can_manage_inventory, can_manage_j35_planner, can_view_admin,
    can_view_all_courses, can_view_dashboard, can_view_inventory,
    can_view_j35_planner, can_view_reports, can_view_staff, can_view_students,
    feature_access,
)
from .models import ActivityLog, Course, CourseInstructor, CourseSession, Instructor, UserSecurityProfile
from .staff_access_models import StaffPermissionOverride
from .staff_groups import J35_PLANNER_GROUP, OPERATIONS_ADMIN_GROUP


class StaffAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="matrix-admin", password="safe-matrix-test")
        self.user = User.objects.create_user(username="matrix-instructor", password="safe-matrix-test")
        self.instructor = Instructor.objects.create(user=self.user, name_english="Matrix instructor", email="instructor@example.com")
        self.course = Course.objects.create(title_english="Matrix course")
        self.session = CourseSession.objects.create(course=self.course)
        self.assigned = CourseSession.objects.create(course=self.course)
        CourseInstructor.objects.create(session=self.assigned, instructor=self.instructor)

    def grant(self, feature, level="view", user=None):
        StaffPermissionOverride.objects.update_or_create(user=user or self.user, feature=feature, defaults={"level": level, "updated_by": self.admin})

    def staff_post(self, **extra):
        payload = {
            "action": "update_instructor", "instructor_id": self.instructor.pk,
            "name": "Matrix instructor", "email": "instructor@example.com",
            "phone": "0500000000", "active": "yes",
        }
        payload.update(extra)
        return payload

    def test_regular_instructor_has_only_students_and_assigned_courses(self):
        self.assertTrue(can_view_students(self.user))
        self.assertTrue(can_access_session(self.user, self.assigned))
        self.assertTrue(can_edit_session(self.user, self.assigned))
        self.assertFalse(can_access_session(self.user, self.session))
        for check in (can_view_dashboard, can_view_j35_planner, can_manage_j35_planner,
                      can_view_inventory, can_view_reports, can_view_staff, can_view_all_courses):
            self.assertFalse(check(self.user), check.__name__)
        self.assertEqual(feature_access(self.user, "unrecognized_module"), "")

    def test_role_and_override_can_grant_view_then_edit_then_inherit(self):
        self.grant("inventory", "view")
        self.assertTrue(can_view_inventory(self.user))
        self.assertFalse(can_manage_inventory(self.user))
        self.grant("inventory", "edit")
        self.assertTrue(can_manage_inventory(self.user))
        StaffPermissionOverride.objects.filter(user=self.user, feature="inventory").delete()
        self.assertFalse(can_view_inventory(self.user))

    def test_view_override_downgrades_administrator_role_edit(self):
        operations = get_user_model().objects.create_user(username="matrix-operations", is_staff=True)
        operations.groups.add(Group.objects.create(name=OPERATIONS_ADMIN_GROUP))
        self.assertTrue(can_manage_j35_planner(operations))
        self.grant("j35", "view", user=operations)
        self.assertTrue(can_view_j35_planner(operations))
        self.assertFalse(can_manage_j35_planner(operations))
        self.grant("staff", "view", user=operations)
        self.assertTrue(can_view_staff(operations))
        self.assertFalse(can_edit_staff(operations))
        self.client.force_login(operations)
        self.assertEqual(self.client.post(reverse("staff"), self.staff_post()).status_code, 403)

    def test_planner_sees_all_courses_without_editing_unassigned_course(self):
        self.user.groups.add(Group.objects.create(name=J35_PLANNER_GROUP))
        self.assertTrue(can_view_dashboard(self.user))
        self.assertTrue(can_view_all_courses(self.user))
        self.assertTrue(can_access_session(self.user, self.session))
        self.assertFalse(can_edit_session(self.user, self.session))

    def test_own_course_view_override_blocks_mutations(self):
        self.grant("own_courses", "view")
        self.assertTrue(can_access_session(self.user, self.assigned))
        self.assertFalse(can_edit_session(self.user, self.assigned))

    def test_directory_view_grants_course_read_not_edit_or_my_courses_all(self):
        self.grant("course_directory", "view")
        self.assertTrue(can_access_session(self.user, self.session))
        self.assertFalse(can_edit_session(self.user, self.session))
        self.assertFalse(can_view_all_courses(self.user))

    def test_view_permission_cannot_create_courses(self):
        self.grant("create_course", "view")
        self.assertFalse(can_create_course(self.user))
        self.grant("create_course", "edit")
        self.assertTrue(can_create_course(self.user))

    def test_explicit_staff_or_admin_edit_does_not_bypass_administrator_gate(self):
        self.grant("staff", "edit")
        self.grant("admin", "edit")
        self.assertTrue(can_view_staff(self.user))
        self.assertFalse(can_edit_staff(self.user))
        self.assertFalse(can_view_admin(self.user))
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse("staff"), self.staff_post()).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 403)

    def test_unauthenticated_and_inactive_users_do_not_receive_explicit_grants(self):
        self.grant("inventory", "edit")
        self.assertFalse(can_view_inventory(AnonymousUser()))
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertFalse(can_view_inventory(self.user))
        self.assertFalse(can_access_session(self.user, self.assigned))

    def test_deactivating_staff_disables_linked_login_and_explicit_grants(self):
        self.grant("inventory", "edit")
        self.client.force_login(self.admin)
        response = self.client.post(reverse("staff"), self.staff_post(active=""))
        self.assertEqual(response.status_code, 302)
        self.instructor.refresh_from_db()
        self.user.refresh_from_db()
        self.assertFalse(self.instructor.active)
        self.assertFalse(self.user.is_active)
        self.assertFalse(can_view_inventory(self.user))
        self.assertFalse(Client().login(username=self.user.username, password="safe-matrix-test"))

    def test_staff_save_preserves_password_updates_email_and_audits_without_secrets(self):
        password_hash = self.user.password
        self.client.force_login(self.admin)
        response = self.client.post(reverse("staff"), self.staff_post(
            email="NEW.EMAIL@EXAMPLE.COM", permissions_present="yes",
            permission_reports="view",
        ))
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.instructor.refresh_from_db()
        self.assertEqual(self.user.password, password_hash)
        self.assertEqual(self.user.email, "new.email@example.com")
        self.assertEqual(self.instructor.email, "new.email@example.com")
        self.assertEqual(self.instructor.phone, "0500000000")
        self.assertTrue(can_view_reports(self.user))
        log = ActivityLog.objects.get(object_type="Instructor", object_id=str(self.instructor.pk))
        self.assertNotIn("password", str(log.details).lower())

    def test_bad_override_rolls_back_contact_and_role_changes(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("staff"), self.staff_post(
            name="Must be rolled back", permissions_present="yes", permission_reports="owner",
        ))
        self.instructor.refresh_from_db()
        self.assertEqual(self.instructor.name_english, "Matrix instructor")
        self.assertFalse(ActivityLog.objects.exists())

    def test_initial_password_change_is_optional_and_password_is_not_logged(self):
        self.client.force_login(self.admin)
        password = "Pine!Zephyr7-Cobalt95"
        response = self.client.post(reverse("staff"), {
            "action": "add_instructor", "name": "New staff member",
            "email": "NEW.STAFF@EXAMPLE.COM", "username": "new-staff-member",
            "temporary_password": password,
        })
        self.assertEqual(response.status_code, 302)
        account = get_user_model().objects.get(username="new-staff-member")
        self.assertTrue(account.check_password(password))
        self.assertFalse(UserSecurityProfile.objects.get(user=account).must_change_password)
        self.assertNotIn(password, str(list(ActivityLog.objects.values("description", "details"))))

    def test_csrf_is_required_for_staff_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(reverse("staff"), self.staff_post()).status_code, 403)

    def test_staff_view_grant_renders_read_only_matrix(self):
        self.grant("staff", "view")
        self.client.force_login(self.user)
        response = self.client.get(reverse("staff"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Matrix instructor")
        self.assertNotContains(response, 'name="temporary_password"')
        self.assertNotContains(response, 'name="permission_inventory"')

    def test_module_urls_and_aliases_enforce_hidden_navigation_permissions(self):
        self.client.force_login(self.user)
        for name in ("inventory", "reports", "staff", "courses_sessions", "courses",
                     "courses_active", "registrations", "data_quality", "j35_planner", "schedule"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("my_courses"), fetch_redirect_response=False)

    def test_inventory_view_cannot_send_edit_request_directly(self):
        self.grant("inventory", "view")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("inventory")).status_code, 200)
        response = self.client.post(reverse("inventory"), {"action": "add_item", "name": "Forbidden item"})
        self.assertEqual(response.status_code, 403)

    def test_course_view_cannot_post_roster_changes_or_generate_frontend(self):
        self.grant("own_courses", "view")
        self.client.force_login(self.user)
        response = self.client.post(reverse("course_roster_save", args=[self.assigned.public_id]), {})
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse("external_upload_xlsx", args=[self.assigned.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_directory_view_cannot_post_registration_updates(self):
        from .models import Registration
        registration = Registration.objects.create(requested_session=self.session, submitted_name_english="Protected student")
        self.grant("course_directory", "view")
        self.client.force_login(self.user)
        response = self.client.post(reverse("registration_detail", args=[registration.public_id]), {"action": "reject"})
        self.assertEqual(response.status_code, 403)
        registration.refresh_from_db()
        self.assertNotEqual(registration.status, Registration.Status.REJECTED)
