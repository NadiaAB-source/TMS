from .access import feature_permissions, is_operations_admin
from .models import Notification


def access_context(request):
    user = request.user
    permissions = feature_permissions(user)
    authenticated = getattr(user, "is_authenticated", False)
    administrator = is_operations_admin(user) if authenticated else False
    return {
        "feature_permissions": permissions,
        "operations_admin": administrator,
        "dashboard_access": bool(permissions["dashboard"]),
        "my_courses_access": bool(permissions["own_courses"] or permissions["all_courses"] or permissions["j35"]),
        "students_access": bool(permissions["students"]),
        "directory_access": bool(permissions["course_directory"]),
        "staff_access": bool(permissions["staff"]),
        "inventory_access": bool(permissions["inventory"]),
        "j35_planner_access": bool(permissions["j35"]),
        "reports_access": bool(permissions["reports"]),
        "data_quality_access": bool(permissions["data_quality"]),
        "admin_access": bool(administrator and getattr(user, "is_staff", False) and permissions["admin"]),
        "can_view_dashboard": bool(permissions["dashboard"]),
        "can_view_students": bool(permissions["students"]),
        "can_view_course_directory": bool(permissions["course_directory"]),
        "can_view_staff": bool(permissions["staff"]),
        "can_view_inventory": bool(permissions["inventory"]),
        "can_view_reports": bool(permissions["reports"]),
        "can_view_data_quality": bool(permissions["data_quality"]),
        "notification_unread_count": Notification.objects.filter(
            recipient=user,
            read_at__isnull=True,
        ).count() if authenticated else 0,
    }
