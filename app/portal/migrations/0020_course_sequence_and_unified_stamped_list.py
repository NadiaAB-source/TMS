from django.db import migrations, models


COURSE_SEQUENCE_COUNTER_KEY = "course_session"


def create_course_sequence_counter(apps, schema_editor):
    counter_model = apps.get_model("portal", "CourseSequenceCounter")
    counter_model.objects.get_or_create(
        key=COURSE_SEQUENCE_COUNTER_KEY,
        defaults={"next_number": 1},
    )


def consolidate_day_two_lists(apps, schema_editor):
    """Treat historic Day 2 uploads as stamped-list versions.

    There is now one document lifecycle: Stamped List.  When older data has
    both document types marked current, retain the newest upload as current and
    preserve every other file as a previous version.
    """

    archive_model = apps.get_model("portal", "StampedListArchive")
    session_ids = (
        archive_model.objects.order_by()
        .values_list("session_id", flat=True)
        .distinct()
    )
    for session_id in session_ids.iterator():
        documents = list(
            archive_model.objects.filter(session_id=session_id).order_by(
                "-uploaded_at", "-id"
            )
        )
        current_id = next(
            (document.pk for document in documents if document.active),
            None,
        )
        for document in documents:
            updates = []
            if document.document_type != "stamped_list":
                document.document_type = "stamped_list"
                updates.append("document_type")
            expected_active = document.pk == current_id if current_id else False
            if document.active != expected_active:
                document.active = expected_active
                updates.append("active")
            if updates:
                document.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0019_secure_staff_j35_documents"),
    ]

    operations = [
        migrations.CreateModel(
            name="CourseSequenceCounter",
            fields=[
                (
                    "key",
                    models.CharField(
                        default="course_session",
                        editable=False,
                        max_length=50,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("next_number", models.PositiveIntegerField(default=1)),
                (
                    "enabled",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Enable only after the approved historical course reset. "
                            "Until then existing and newly created sessions keep "
                            "their current display."
                        ),
                    ),
                ),
            ],
            options={
                "verbose_name": "course sequence counter",
                "verbose_name_plural": "course sequence counters",
            },
        ),
        migrations.AddField(
            model_name="coursesession",
            name="sequence_number",
            field=models.PositiveIntegerField(
                blank=True,
                db_index=True,
                help_text=(
                    "Permanent chronological course-session number. Existing "
                    "records remain unnumbered until the planned historical reset "
                    "and import."
                ),
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(
            create_course_sequence_counter,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            consolidate_day_two_lists,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="stampedlistarchive",
            name="document_type",
            field=models.CharField(
                choices=[("stamped_list", "Stamped List")],
                db_index=True,
                default="stamped_list",
                max_length=20,
            ),
        ),
    ]
