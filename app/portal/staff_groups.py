"""Least-privilege staff groups and role-to-group synchronisation."""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType


OPERATIONS_ADMIN_GROUP = "TMS Operations Administrators"
J35_PLANNER_GROUP = "J35 Planners"
TRAINING_MANAGER_GROUP = "Training Managers"
LEAD_INSTRUCTOR_GROUP = "Lead Instructors"
INSTRUCTOR_GROUP = "Instructors"
INVENTORY_SUPERVISOR_GROUP = "Inventory Supervisors"
REPORTING_OFFICER_GROUP = "Reporting Officers"

PREDEFINED_STAFF_POSITIONS = (
    ("Training Director", "Full operations administration."),
    ("Training Manager", "Course and training workflow management."),
    ("Lead Instructor", "Lead course delivery and shared workflow."),
    ("Instructor", "Assigned course delivery and records."),
    ("J35 Planner", "J35 instructor allocation planning."),
    ("Inventory Supervisor", "Inventory issue and refill control."),
    ("Reporting Officer", "Read-only reporting access."),
    ("Project Coordinator", "Operations administration."),
)

MANAGED_GROUP_NAMES = (
    OPERATIONS_ADMIN_GROUP,
    J35_PLANNER_GROUP,
    TRAINING_MANAGER_GROUP,
    LEAD_INSTRUCTOR_GROUP,
    INSTRUCTOR_GROUP,
    INVENTORY_SUPERVISOR_GROUP,
    REPORTING_OFFICER_GROUP,
)

ADMINISTRATOR_ROLE_NAMES = {
    "li",
    "lead instructor",
    "training director",
    "training manager",
    "project coordinator",
}

LEAD_INSTRUCTOR_ROLE_NAMES = {"li", "lead instructor"}


def _role_names(instructor):
    return {
        role.name.strip().casefold()
        for role in instructor.roles.all()
        if role.active and (role.name or "").strip()
    }


def group_names_for_instructor(instructor):
    """Map configured Staff Positions to the smallest useful access set."""

    roles = _role_names(instructor)
    groups = {INSTRUCTOR_GROUP}

    if roles & ADMINISTRATOR_ROLE_NAMES:
        groups.add(OPERATIONS_ADMIN_GROUP)
    if roles & {"training manager", "training director"}:
        groups.add(TRAINING_MANAGER_GROUP)
    if roles & LEAD_INSTRUCTOR_ROLE_NAMES:
        groups.add(LEAD_INSTRUCTOR_GROUP)
    # Lead instructors own the weekly distribution plan.  They receive the
    # narrow J35 Planner capability as well as their normal course role;
    # other instructors have no planning access unless an administrator
    # grants it explicitly in the Staff permission matrix.
    if roles & LEAD_INSTRUCTOR_ROLE_NAMES or any(
        "j35" in role or "planner" in role for role in roles
    ):
        groups.add(J35_PLANNER_GROUP)
    if any("report" in role for role in roles):
        groups.add(REPORTING_OFFICER_GROUP)
    if instructor.is_inventory_supervisor or "inventory supervisor" in roles:
        groups.add(INVENTORY_SUPERVISOR_GROUP)
    return groups


def _permissions_for_group(group_name):
    """Return explicit permissions; no managed group receives delete rights."""

    from .models import (
        ActivityLog,
        Camp,
        CampContact,
        Course,
        CourseInstructor,
        CourseSession,
        Instructor,
        InstructorAllocation,
        InstructorInventoryBalance,
        InstructorInventoryMovement,
        InstructorRole,
        InventoryItem,
        J35TrainingNeed,
        LoginAttempt,
        Notification,
        Registration,
        StampedListArchive,
        Team,
        TrainingRecord,
        UserSecurityProfile,
    )
    from .staff_access_models import StaffPermissionOverride

    def model_permissions(*models, actions=("view",)):
        content_types = ContentType.objects.get_for_models(*models).values()
        codenames = {
            f"{action}_{content_type.model}"
            for content_type in content_types
            for action in actions
        }
        return Permission.objects.filter(
            content_type__in=content_types,
            codename__in=codenames,
        )

    all_portal_models = (
        ActivityLog,
        Camp,
        CampContact,
        Course,
        CourseInstructor,
        CourseSession,
        Instructor,
        InstructorAllocation,
        InstructorInventoryBalance,
        InstructorInventoryMovement,
        InstructorRole,
        InventoryItem,
        J35TrainingNeed,
        LoginAttempt,
        Notification,
        Registration,
        StampedListArchive,
        Team,
        TrainingRecord,
        UserSecurityProfile,
        StaffPermissionOverride,
    )
    if group_name == OPERATIONS_ADMIN_GROUP:
        portal = model_permissions(
            *all_portal_models,
            actions=("add", "change", "view"),
        )
        auth = Permission.objects.filter(
            content_type__app_label="auth",
            codename__in=(
                "view_user",
                "change_user",
                "view_group",
                "change_group",
            ),
        )
        return portal | auth
    if group_name == J35_PLANNER_GROUP:
        # This group can work the planning pipeline and publish the resulting
        # assignments.  It can see the catalogue data required to make a
        # decision, but it does not receive broad operations/admin rights or
        # delete permissions.
        return model_permissions(
            Camp,
            Course,
            CourseSession,
            Instructor,
            InstructorRole,
            Team,
            ActivityLog,
            actions=("view",),
        ) | model_permissions(
            CampContact,
            J35TrainingNeed,
            InstructorAllocation,
            actions=("add", "change", "view"),
        ) | model_permissions(
            # These are generated by confirmation; planners can create and
            # inspect them, but cannot alter a published course assignment or
            # another person's notification through this role.
            CourseInstructor,
            Notification,
            actions=("add", "view"),
        )
    if group_name in {TRAINING_MANAGER_GROUP, LEAD_INSTRUCTOR_GROUP}:
        return model_permissions(
            Course,
            CourseSession,
            CourseInstructor,
            Registration,
            TrainingRecord,
            StampedListArchive,
            actions=("add", "change", "view"),
        )
    if group_name == INVENTORY_SUPERVISOR_GROUP:
        return model_permissions(
            InventoryItem,
            InstructorInventoryBalance,
            InstructorInventoryMovement,
            actions=("add", "change", "view"),
        )
    if group_name == REPORTING_OFFICER_GROUP:
        return model_permissions(*all_portal_models, actions=("view",))
    return model_permissions(
        CourseSession,
        CourseInstructor,
        Registration,
        TrainingRecord,
        StampedListArchive,
        actions=("view",),
    )


def ensure_predefined_staff_positions():
    """Add the standard selectable Staff Positions without overwriting edits."""

    from .models import InstructorRole

    positions = {}
    for name, description in PREDEFINED_STAFF_POSITIONS:
        position, _ = InstructorRole.objects.get_or_create(
            name=name,
            defaults={"description": description, "active": True},
        )
        positions[name] = position
    return positions


def ensure_predefined_groups():
    """Create or refresh the managed groups without changing other groups."""

    groups = {}
    for name in MANAGED_GROUP_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(_permissions_for_group(name))
        groups[name] = group
    return groups


def synchronise_staff_groups(instructor, groups=None):
    """Synchronise only IQARUS-managed groups for an existing staff account."""

    if not instructor.user_id:
        return set()
    groups = groups or ensure_predefined_groups()
    wanted_names = group_names_for_instructor(instructor)
    user = instructor.user
    managed = list(
        Group.objects.filter(name__in=MANAGED_GROUP_NAMES)
    )
    user.groups.remove(*managed)
    user.groups.add(*(groups[name] for name in wanted_names))
    user.is_active = instructor.active
    user.is_staff = user.is_superuser or (
        OPERATIONS_ADMIN_GROUP in wanted_names
    )
    user.save(update_fields=["is_active", "is_staff"])
    return wanted_names
