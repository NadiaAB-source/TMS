"""Apply the same module permissions to URLs and navigation, including aliases."""
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils.deprecation import MiddlewareMixin

from .access import can_edit_admin, can_view_admin, feature_access, require_session_access


class ModuleAccessMiddleware(MiddlewareMixin):
    MODULES = {
        "students": "students", "students_print": "students", "student_detail": "students",
        "training_records": "students", "training_records_print": "students",
        "courses_active": "course_directory", "courses_workspace": "course_directory",
        "courses_sessions": "course_directory", "courses": "course_directory",
        "course_session_detail": "course_directory", "reports": "reports",
        "inventory": "inventory", "staff": "staff", "data_quality": "data_quality",
        "j35_planner": "j35", "schedule": "j35", "j35_grid_save": "j35",
        "j35_grid_delete": "j35",
    }
    READ_POSTS = {"students_print", "training_records_print"}
    COURSE_WRITES = {
        "course_edit", "course_cancel", "course_delete", "external_upload_complete",
        "resend_course_information", "course_roster_save", "course_roster_save_download",
        "course_inventory_save", "external_upload_xlsx",
    }
    COURSE_READS = {"instructor_course_workspace", "stamped_lists", "registration_qr", "registration_qr_image"}

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = request.user
        if not user.is_authenticated:
            return None  # Existing login and public-registration handlers own this state.
        match = request.resolver_match
        name = match.url_name if match else ""
        if match and match.namespace == "admin":
            # Django's own model permissions still apply inside the admin site.
            if not can_view_admin(user) or (request.method not in {"GET", "HEAD", "OPTIONS"} and not can_edit_admin(user)):
                raise PermissionDenied("Administration access is not assigned to your account.")
        feature = self.MODULES.get(name)
        if feature:
            level = feature_access(user, feature)
            write = request.method not in {"GET", "HEAD", "OPTIONS"} and name not in self.READ_POSTS
            if not level or (write and level != "edit"):
                raise PermissionDenied("Your account does not have permission for this action.")
        if name in {"registrations", "registrations_active", "registration_detail"}:
            levels = (feature_access(user, "course_directory"), feature_access(user, "data_quality"))
            unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
            if not any(levels) or (unsafe and "edit" not in levels):
                raise PermissionDenied("Registration-directory access is not assigned to your account.")
        if name == "course_create" and feature_access(user, "create_course") != "edit":
            raise PermissionDenied("Course creation requires Edit access.")
        if name == "my_courses" and not (
            feature_access(user, "own_courses") or feature_access(user, "all_courses") or feature_access(user, "j35")
        ):
            raise PermissionDenied("Course access is not assigned to your account.")
        if (name in self.COURSE_WRITES or name in self.COURSE_READS) and view_kwargs.get("public_id"):
            from .models import CourseSession
            session = get_object_or_404(CourseSession, public_id=view_kwargs["public_id"])
            edit = name in self.COURSE_WRITES or request.method not in {"GET", "HEAD", "OPTIONS"}
            require_session_access(user, session, edit=edit)
        return None
