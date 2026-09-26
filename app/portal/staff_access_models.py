"""Per-account module grants layered over the predefined staff roles."""
from django.conf import settings
from django.db import models

STAFF_FEATURES = (
    ("dashboard", "Dashboard"), ("j35", "J35 planning"),
    ("create_course", "Create course"), ("all_courses", "All courses"),
    ("own_courses", "My courses"), ("students", "Students"),
    ("course_directory", "Directory"), ("staff", "Staff"),
    ("inventory", "Inventory"), ("reports", "Reports"),
    ("data_quality", "Data quality"), ("admin", "Administration"),
)


class StaffPermissionOverride(models.Model):
    """A View grant may downgrade inherited Edit; absence uses role defaults."""
    class Level(models.TextChoices):
        VIEW = "view", "View"
        EDIT = "edit", "Edit"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tms_permission_overrides")
    feature = models.CharField(max_length=32, choices=STAFF_FEATURES)
    level = models.CharField(max_length=4, choices=Level.choices)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tms_permission_changes")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user_id", "feature"]
        constraints = [
            models.UniqueConstraint(fields=["user", "feature"], name="unique_staff_feature_override"),
            models.CheckConstraint(condition=models.Q(level__in=["view", "edit"]), name="staff_override_valid_level"),
            models.CheckConstraint(condition=models.Q(feature__in=[key for key, _ in STAFF_FEATURES]), name="staff_override_valid_feature"),
        ]
        verbose_name = "Staff permission override"

    def __str__(self):
        return f"{self.user}: {self.get_feature_display()} — {self.get_level_display()}"
