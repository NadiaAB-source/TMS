from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0016_course_controls_instructor_inventory"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="registration",
            name="duplicate_review_status",
            field=models.CharField(
                choices=[
                    ("clear", "No duplicate found"),
                    ("review_required", "Review required"),
                    ("reviewed_allow", "Reviewed — allow"),
                    ("reviewed_exclude", "Reviewed — exclude"),
                ],
                db_index=True,
                default="clear",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="registration",
            name="duplicate_review_fingerprint",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="registration",
            name="duplicate_review_notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="registration",
            name="duplicate_reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="registration",
            name="duplicate_reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_registration_duplicates",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]

