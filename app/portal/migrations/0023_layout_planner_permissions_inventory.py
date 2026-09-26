"""Add planning metadata, individual module grants and auditable stock issues."""
import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0022_j35_weekly_pipeline"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffPermissionOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("feature", models.CharField(max_length=32, choices=[
                    ("dashboard", "Dashboard"), ("j35", "J35 planning"),
                    ("create_course", "Create course"), ("all_courses", "All courses"),
                    ("own_courses", "My courses"), ("students", "Students"),
                    ("course_directory", "Directory"), ("staff", "Staff"),
                    ("inventory", "Inventory"), ("reports", "Reports"),
                    ("data_quality", "Data quality"), ("admin", "Administration"),
                ])),
                ("level", models.CharField(max_length=4, choices=[("view", "View"), ("edit", "Edit")])),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tms_permission_changes", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tms_permission_overrides", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["user_id", "feature"],
                "verbose_name": "Staff permission override",
                "constraints": [
                    models.UniqueConstraint(fields=("user", "feature"), name="unique_staff_feature_override"),
                    models.CheckConstraint(condition=models.Q(level__in=["view", "edit"]), name="staff_override_valid_level"),
                    models.CheckConstraint(condition=models.Q(feature__in=["dashboard", "j35", "create_course", "all_courses", "own_courses", "students", "course_directory", "staff", "inventory", "reports", "data_quality", "admin"]), name="staff_override_valid_feature"),
                ],
            },
        ),
        migrations.CreateModel(
            name="J35GridCell",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("color", models.CharField(default="#ffffff", max_length=7, validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Choose a valid cell colour.")])),
                ("revision", models.PositiveIntegerField(default=1)),
                ("owns_assignment", models.BooleanField(default=False, editable=False)),
                ("allocation", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="grid_cell", to="portal.instructorallocation")),
                ("course", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="j35_grid_cells", to="portal.course")),
            ],
            options={"verbose_name": "J35 grid cell"},
        ),
        migrations.CreateModel(
            name="J35GridCourseLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_j35_grid_courses", to=settings.AUTH_USER_MODEL)),
                ("session", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="j35_grid_origin", to="portal.coursesession")),
            ],
        ),
        migrations.CreateModel(
            name="WarehouseStockPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("minimum_quantity", models.PositiveIntegerField(default=0)),
                ("maximum_quantity", models.PositiveIntegerField(default=0)),
                ("lead_time_days", models.PositiveIntegerField(default=0)),
                ("item", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="stock_policy", to="portal.inventoryitem")),
            ],
        ),
        migrations.CreateModel(
            name="InventoryIssue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("notes", models.CharField(blank=True, max_length=1000)),
                ("issued_on", models.DateField(default=django.utils.timezone.localdate)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reversed_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_stock_issues", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="updated_stock_issues", to=settings.AUTH_USER_MODEL)),
                ("reversed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reversed_stock_issues", to=settings.AUTH_USER_MODEL)),
                ("instructor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="stock_issues", to="portal.instructor")),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="InventoryIssueLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField()),
                ("issue", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lines", to="portal.inventoryissue")),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="issue_lines", to="portal.inventoryitem")),
            ],
            options={
                "ordering": ["item__name", "id"],
                "constraints": [
                    models.UniqueConstraint(fields=("issue", "item"), name="unique_stock_issue_item"),
                    models.CheckConstraint(condition=models.Q(quantity__gt=0), name="stock_issue_positive_quantity"),
                ],
            },
        ),
    ]
