import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0021_normalize_inventory_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CampContact",
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
                ("name", models.CharField(max_length=200)),
                ("phone", models.CharField(blank=True, max_length=80)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("position", models.CharField(blank=True, max_length=150)),
                ("active", models.BooleanField(default=True)),
                ("is_primary", models.BooleanField(default=False)),
                ("notes", models.TextField(blank=True)),
                (
                    "camp",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="contacts",
                        to="portal.camp",
                    ),
                ),
            ],
            options={"ordering": ["camp__name", "-is_primary", "name"]},
        ),
        migrations.CreateModel(
            name="J35TrainingNeed",
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
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("request", "Client request"),
                            ("outreach", "Proactive outreach"),
                        ],
                        db_index=True,
                        default="outreach",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("new", "New"),
                            ("contacted", "Contacted"),
                            ("requested", "Requested"),
                            ("proposed", "Proposed"),
                            ("confirmed", "Confirmed"),
                            ("deferred", "Deferred"),
                            ("closed", "Closed"),
                        ],
                        db_index=True,
                        default="new",
                        max_length=20,
                    ),
                ),
                (
                    "priority",
                    models.CharField(
                        choices=[
                            ("normal", "Normal"),
                            ("high", "High"),
                            ("urgent", "Urgent"),
                        ],
                        db_index=True,
                        default="normal",
                        max_length=12,
                    ),
                ),
                (
                    "contacted_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "requested_start_date",
                    models.DateField(blank=True, db_index=True, null=True),
                ),
                ("requested_end_date", models.DateField(blank=True, null=True)),
                (
                    "expected_capacity",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("required_instructors", models.PositiveSmallIntegerField(default=1)),
                ("notes", models.TextField(blank=True)),
                ("contact_notes", models.TextField(blank=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "camp",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="j35_training_needs",
                        to="portal.camp",
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="training_needs",
                        to="portal.campcontact",
                    ),
                ),
                (
                    "confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="confirmed_j35_training_needs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "course",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="j35_training_needs",
                        to="portal.course",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_j35_training_needs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "preferred_team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="preferred_j35_training_needs",
                        to="portal.team",
                    ),
                ),
                (
                    "proposed_session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="j35_training_needs",
                        to="portal.coursesession",
                    ),
                ),
                (
                    "required_role",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="required_j35_training_needs",
                        to="portal.instructorrole",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_j35_training_needs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["requested_start_date", "-created_at"]},
        ),
        migrations.AddField(
            model_name="instructorallocation",
            name="allocation_kind",
            field=models.CharField(
                choices=[
                    ("course", "Course"),
                    ("leave", "Leave"),
                    ("admin", "Administrative duty"),
                    ("other", "Other"),
                ],
                db_index=True,
                default="other",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="instructorallocation",
            name="notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instructorallocation",
            name="override_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="instructorallocation",
            name="training_need",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="allocations",
                to="portal.j35trainingneed",
            ),
        ),
        migrations.AddIndex(
            model_name="campcontact",
            index=models.Index(
                fields=["camp", "active", "is_primary"],
                name="portal_campcontact_active_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="campcontact",
            constraint=models.UniqueConstraint(
                fields=("camp", "name"),
                name="unique_camp_contact_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="campcontact",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_primary=True, active=True),
                fields=("camp",),
                name="unique_primary_camp_contact",
            ),
        ),
        migrations.AddIndex(
            model_name="j35trainingneed",
            index=models.Index(
                fields=["camp", "status", "requested_start_date"],
                name="portal_j35need_camp_st_dt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="j35trainingneed",
            index=models.Index(
                fields=["status", "requested_start_date"],
                name="portal_j35need_status_dt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="j35trainingneed",
            index=models.Index(
                fields=["camp", "contacted_at"],
                name="portal_j35need_contacted_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="instructorallocation",
            index=models.Index(
                fields=["training_need", "status"],
                name="portal_j35alloc_need_st_idx",
            ),
        ),
    ]
