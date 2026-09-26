from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0015_assigned_instructors"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="activitylog",
            name="action",
            field=models.CharField(
                choices=[
                    ("create", "Create"),
                    ("update", "Update"),
                    ("approve", "Approve"),
                    ("reject", "Reject"),
                    ("import", "Import"),
                    ("export", "Export"),
                    ("print", "Print"),
                    ("login", "Login"),
                    ("cancel", "Cancel"),
                    ("delete", "Delete"),
                    ("issue", "Issue"),
                    ("refill", "Refill"),
                    ("other", "Other"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="InstructorInventoryBalance",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity_on_hand", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                (
                    "instructor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_balances",
                        to="portal.instructor",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="instructor_balances",
                        to="portal.inventoryitem",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_instructor_inventory_balances",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": [
                    "instructor__name_english",
                    "item__category",
                    "item__name",
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="instructorinventorybalance",
            constraint=models.UniqueConstraint(
                fields=("instructor", "item"),
                name="unique_instructor_inventory_item",
            ),
        ),
        migrations.CreateModel(
            name="InstructorInventoryMovement",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "movement_type",
                    models.CharField(
                        choices=[
                            ("issue", "Initial issue"),
                            ("refill", "Refill"),
                            ("course_report", "Course use report"),
                            ("correction", "Correction"),
                        ],
                        max_length=30,
                    ),
                ),
                ("quantity_change", models.IntegerField()),
                ("balance_after", models.PositiveIntegerField()),
                ("notes", models.TextField(blank=True)),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "instructor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_movements",
                        to="portal.instructor",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="instructor_movements",
                        to="portal.inventoryitem",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recorded_instructor_inventory_movements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="instructor_inventory_movements",
                        to="portal.coursesession",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["instructor", "created_at"],
                        name="portal_iim_instr_created",
                    ),
                    models.Index(
                        fields=["item", "created_at"],
                        name="portal_iim_item_created",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="CourseInstructorInventoryUsage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity_used", models.PositiveIntegerField(default=0)),
                ("quantity_consumed", models.PositiveIntegerField(default=0)),
                ("quantity_deteriorated", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                (
                    "instructor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="course_inventory_usage",
                        to="portal.instructor",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="instructor_course_usage",
                        to="portal.inventoryitem",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recorded_course_instructor_inventory_usage",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="instructor_inventory_usage",
                        to="portal.coursesession",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "instructor__name_english",
                    "item__category",
                    "item__name",
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="courseinstructorinventoryusage",
            constraint=models.UniqueConstraint(
                fields=("session", "instructor", "item"),
                name="unique_course_instructor_inventory_item",
            ),
        ),
    ]

