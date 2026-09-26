"""Presentation metadata for the J35 spreadsheet, preserving existing allocations."""

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class J35GridCell(models.Model):
    allocation = models.OneToOneField(
        "InstructorAllocation", on_delete=models.CASCADE, related_name="grid_cell"
    )
    course = models.ForeignKey(
        "Course", null=True, blank=True, on_delete=models.PROTECT,
        related_name="j35_grid_cells",
    )
    color = models.CharField(
        max_length=7, default="#ffffff",
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Choose a valid cell colour.")],
    )
    revision = models.PositiveIntegerField(default=1)
    owns_assignment = models.BooleanField(default=False, editable=False)

    class Meta:
        verbose_name = "J35 grid cell"


class J35GridCourseLink(models.Model):
    """Marks course sessions created by the grid, including retained empty drafts."""

    session = models.OneToOneField(
        "CourseSession", on_delete=models.CASCADE, related_name="j35_grid_origin"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="created_j35_grid_courses",
    )
    created_at = models.DateTimeField(auto_now_add=True)
