import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def normalize_existing_emails(apps, schema_editor):
    """Canonicalise historic email values without changing other records."""

    email_fields = (
        ("Student", "email"),
        ("Instructor", "email"),
        ("Registration", "email_raw"),
        ("CourseRosterSnapshotItem", "email"),
        ("StudentIdentityProposal", "proposed_email"),
    )
    for model_name, field_name in email_fields:
        model = apps.get_model("portal", model_name)
        for record in model.objects.all().only("pk", field_name).iterator():
            normalized = str(getattr(record, field_name) or "").strip().lower()
            if normalized != getattr(record, field_name):
                model.objects.filter(pk=record.pk).update(
                    **{field_name: normalized}
                )

    user_model = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    for user in user_model.objects.all().only("pk", "email").iterator():
        normalized = str(user.email or "").strip().lower()
        if normalized != user.email:
            user_model.objects.filter(pk=user.pk).update(email=normalized)


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0018_coursesession_service_branch"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InstructorAllocation",
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
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("activity", models.CharField(max_length=200)),
                ("start_date", models.DateField(db_index=True)),
                ("end_date", models.DateField(db_index=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("published", "Published"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=20,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                (
                    "camp",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="j35_allocations",
                        to="portal.camp",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_j35_allocations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "instructor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="j35_allocations",
                        to="portal.instructor",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="j35_allocations",
                        to="portal.coursesession",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_j35_allocations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["start_date", "end_date", "instructor__name_english"],
            },
        ),
        migrations.CreateModel(
            name="LoginAttempt",
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
                ("key_hash", models.CharField(max_length=64, unique=True)),
                ("failure_count", models.PositiveIntegerField(default=0)),
                ("window_started_at", models.DateTimeField()),
                ("last_attempt_at", models.DateTimeField()),
                (
                    "locked_until",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
            ],
            options={"ordering": ["-last_attempt_at"]},
        ),
        migrations.CreateModel(
            name="UserSecurityProfile",
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
                (
                    "must_change_password",
                    models.BooleanField(db_index=True, default=False),
                ),
                (
                    "temporary_password_issued_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("password_changed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tms_security_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="stampedlistarchive",
            name="document_type",
            field=models.CharField(
                choices=[
                    ("stamped_list", "Stamped List"),
                    ("day_two_list", "Day 2 List"),
                ],
                db_index=True,
                default="stamped_list",
                max_length=20,
            ),
        ),
        migrations.AlterModelOptions(
            name="instructor",
            options={
                "ordering": ["name_english"],
                "verbose_name": "Staff member",
                "verbose_name_plural": "Staff",
            },
        ),
        migrations.AlterModelOptions(
            name="instructorrole",
            options={
                "ordering": ["name"],
                "verbose_name": "Staff position",
                "verbose_name_plural": "Staff positions",
            },
        ),
        migrations.AddIndex(
            model_name="activitylog",
            index=models.Index(
                fields=["action", "created_at"],
                name="portal_act_action_dt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="instructorallocation",
            index=models.Index(
                fields=["instructor", "start_date", "end_date"],
                name="portal_j35_instr_dt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="instructorallocation",
            index=models.Index(
                fields=["status", "start_date"],
                name="portal_j35_status_dt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="stampedlistarchive",
            index=models.Index(
                fields=["session", "document_type", "active"],
                name="portal_stamp_session_type_idx",
            ),
        ),
        migrations.RunPython(normalize_existing_emails, migrations.RunPython.noop),
    ]
