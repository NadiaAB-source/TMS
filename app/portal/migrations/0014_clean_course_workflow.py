import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import uuid


def add_administrator_roles(apps, schema_editor):
    InstructorRole = apps.get_model("portal", "InstructorRole")
    for name, description in [
        ("Training Director", "Training operation administrator"),
        ("Training Manager", "Training operation administrator"),
        ("Project Coordinator", "Training operation administrator"),
    ]:
        InstructorRole.objects.get_or_create(
            name=name,
            defaults={"description": description, "active": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0013_delete_coursestudenttag"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="leader",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="led_teams",
                to="portal.instructor",
            ),
        ),
        migrations.AddField(
            model_name="instructor",
            name="is_inventory_supervisor",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="instructor_student_ratio",
            field=models.PositiveIntegerField(
                default=10,
                help_text="Expected students per instructor.",
            ),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="poc_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="poc_contact_number",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="poc_location_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_course_sessions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="course_information_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="external_upload_generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="external_upload_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="external_upload_confirmed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="confirmed_external_course_uploads",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="courseinstructor",
            name="notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="courseinstructor",
            name="acknowledged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RemoveConstraint(
            model_name="courseinstructor",
            name="unique_session_instructor_role",
        ),
        migrations.AddConstraint(
            model_name="courseinstructor",
            constraint=models.UniqueConstraint(
                fields=("session", "instructor"),
                name="unique_session_instructor",
            ),
        ),
        migrations.AddField(
            model_name="registration",
            name="selected_for_roster",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="registration",
            name="is_hp",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="registration",
            name="is_ttt",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="registration",
            name="instructor_remarks",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="InventoryItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=200, unique=True)),
                ("category", models.CharField(choices=[("equipment", "Equipment"), ("consumable", "Consumable")], default="equipment", max_length=20)),
                ("unit", models.CharField(default="item", max_length=50)),
                ("quantity_on_hand", models.PositiveIntegerField(default=0)),
                ("active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
            ],
            options={"ordering": ["category", "name"]},
        ),
        migrations.CreateModel(
            name="CourseInventoryUsage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity_issued", models.PositiveIntegerField(default=0)),
                ("quantity_returned", models.PositiveIntegerField(default=0)),
                ("quantity_consumed", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="course_usage", to="portal.inventoryitem")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_inventory_usage", to=settings.AUTH_USER_MODEL)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inventory_usage", to="portal.coursesession")),
            ],
            options={"ordering": ["item__name"]},
        ),
        migrations.AddConstraint(
            model_name="courseinventoryusage",
            constraint=models.UniqueConstraint(fields=("session", "item"), name="unique_course_inventory_item"),
        ),
        migrations.CreateModel(
            name="CourseRosterSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("version", models.PositiveIntegerField()),
                ("stored_path", models.CharField(max_length=500, unique=True)),
                ("sha256", models.CharField(max_length=64)),
                ("student_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_course_rosters", to=settings.AUTH_USER_MODEL)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="roster_snapshots", to="portal.coursesession")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="courserostersnapshot",
            constraint=models.UniqueConstraint(fields=("session", "version"), name="unique_course_roster_version"),
        ),
        migrations.CreateModel(
            name="CourseRosterSnapshotItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("serial_number", models.PositiveIntegerField()),
                ("email", models.CharField(blank=True, max_length=255)),
                ("eid", models.CharField(blank=True, max_length=64)),
                ("name_english", models.CharField(blank=True, max_length=255)),
                ("name_arabic", models.CharField(blank=True, max_length=255)),
                ("registration", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="roster_snapshot_items", to="portal.registration")),
                ("snapshot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="portal.courserostersnapshot")),
            ],
            options={"ordering": ["serial_number"]},
        ),
        migrations.AddConstraint(
            model_name="courserostersnapshotitem",
            constraint=models.UniqueConstraint(fields=("snapshot", "serial_number"), name="unique_roster_snapshot_serial"),
        ),
        migrations.RunPython(add_administrator_roles, migrations.RunPython.noop),
    ]

