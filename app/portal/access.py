"""Shared module and course-object authorization.

The navigation and request handlers use identical feature rules. Ordinary
instructors receive their assigned courses and the student directory.
"""
from django.core.exceptions import PermissionDenied

from .models import CourseInstructor, Instructor
from .staff_access_models import STAFF_FEATURES, StaffPermissionOverride
from .staff_groups import (
    ADMINISTRATOR_ROLE_NAMES, INSTRUCTOR_GROUP, INVENTORY_SUPERVISOR_GROUP,
    J35_PLANNER_GROUP, LEAD_INSTRUCTOR_GROUP, OPERATIONS_ADMIN_GROUP,
    REPORTING_OFFICER_GROUP, TRAINING_MANAGER_GROUP, group_names_for_instructor,
)

ADMINISTRATOR_ROLES = ADMINISTRATOR_ROLE_NAMES


def _active_user(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_active", False))


def instructor_for_user(user):
    if not _active_user(user):
        return None
    return Instructor.objects.filter(user=user, active=True).prefetch_related("roles").first()


def _group_names(user, instructor=None):
    names = {group.name for group in user.groups.all()}
    instructor = instructor or instructor_for_user(user)
    if instructor and instructor.active:
        names |= group_names_for_instructor(instructor)
    return names


def is_operations_admin(user):
    """Existing administrator identity gate, independent of module overrides."""
    if not _active_user(user):
        return False
    return bool(user.is_superuser or OPERATIONS_ADMIN_GROUP in _group_names(user))


def role_feature_access(user, *, instructor=None):
    permissions = {feature: "" for feature, _ in STAFF_FEATURES}
    if not _active_user(user):
        return permissions
    groups = _group_names(user, instructor=instructor)
    if user.is_superuser or OPERATIONS_ADMIN_GROUP in groups:
        return {feature: "edit" for feature, _ in STAFF_FEATURES}
    if groups & {INSTRUCTOR_GROUP, TRAINING_MANAGER_GROUP, LEAD_INSTRUCTOR_GROUP}:
        permissions.update(own_courses="edit", students="view")
    if J35_PLANNER_GROUP in groups:
        permissions.update(
            dashboard="view", j35="edit", all_courses="view",
            own_courses="edit", students="view", course_directory="view",
        )
    if INVENTORY_SUPERVISOR_GROUP in groups:
        permissions["inventory"] = "edit"
    if REPORTING_OFFICER_GROUP in groups:
        permissions.update(dashboard="view", reports="view")
    return permissions


def feature_permissions(user, *, instructor=None):
    permissions = role_feature_access(user, instructor=instructor)
    if not _active_user(user) or user.is_superuser:
        return permissions
    overrides = {item.feature: item.level for item in user.tms_permission_overrides.all()}
    permissions.update({key: value for key, value in overrides.items() if key in permissions})
    if permissions["j35"] and not permissions["dashboard"]:
        permissions["dashboard"] = "view"
    return permissions


def feature_access(user, feature):
    return feature_permissions(user).get(feature, "")


def can_view_feature(user, feature):
    return feature_access(user, feature) in {"view", "edit"}


def can_edit_feature(user, feature):
    return feature_access(user, feature) == "edit"


def require_feature_access(user, feature, *, edit=False):
    allowed = can_edit_feature(user, feature) if edit else can_view_feature(user, feature)
    if not allowed:
        raise PermissionDenied("Your account does not have access to this action.")


def can_view_dashboard(user):
    return can_view_feature(user, "dashboard")


def can_edit_dashboard(user):
    return can_edit_feature(user, "dashboard")


def can_view_all_courses(user):
    permissions = feature_permissions(user)
    return bool(permissions["all_courses"] or permissions["j35"])


def can_edit_all_courses(user):
    return can_edit_feature(user, "all_courses")


def can_view_my_courses(user):
    permissions = feature_permissions(user)
    return bool(permissions["own_courses"] or permissions["all_courses"] or permissions["j35"])


def can_create_course(user):
    return can_edit_feature(user, "create_course")


def can_access_session(user, session):
    if not _active_user(user):
        return False
    if can_view_all_courses(user) or can_view_course_directory(user):
        return True
    if not can_view_feature(user, "own_courses"):
        return False
    instructor = instructor_for_user(user)
    return bool(instructor and CourseInstructor.objects.filter(session=session, instructor=instructor).exists())


def can_edit_session(user, session):
    if not can_access_session(user, session):
        return False
    if can_edit_all_courses(user):
        return True
    if not can_edit_feature(user, "own_courses"):
        return False
    instructor = instructor_for_user(user)
    return bool(instructor and CourseInstructor.objects.filter(session=session, instructor=instructor).exists())


def require_session_access(user, session, *, edit=False):
    allowed = can_edit_session(user, session) if edit else can_access_session(user, session)
    if not allowed:
        raise PermissionDenied("This course or action is not assigned to your account.")


def can_view_staff(user):
    return can_view_feature(user, "staff")


def can_edit_staff(user):
    return is_operations_admin(user) and can_edit_feature(user, "staff")


def can_view_course_directory(user):
    return can_view_feature(user, "course_directory")


def can_edit_course_directory(user):
    return can_edit_feature(user, "course_directory")


def can_view_students(user):
    return can_view_feature(user, "students")


def can_edit_students(user):
    return can_edit_feature(user, "students")


def can_view_data_quality(user):
    return can_view_feature(user, "data_quality")


def can_edit_data_quality(user):
    return can_edit_feature(user, "data_quality")


def can_manage_inventory(user):
    return can_edit_feature(user, "inventory")


def can_view_inventory(user):
    return can_view_feature(user, "inventory")


def can_manage_j35_planner(user):
    return can_edit_feature(user, "j35")


def can_view_j35_planner(user):
    return can_view_feature(user, "j35")


def can_view_reports(user):
    return can_view_feature(user, "reports")


def can_edit_reports(user):
    return can_edit_feature(user, "reports")


def can_view_admin(user):
    return is_operations_admin(user) and bool(getattr(user, "is_staff", False)) and can_view_feature(user, "admin")


def can_edit_admin(user):
    return can_view_admin(user) and can_edit_feature(user, "admin")
