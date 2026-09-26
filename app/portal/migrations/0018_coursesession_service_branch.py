from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0017_registration_duplicate_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="coursesession",
            name="service_branch",
            field=models.CharField(
                blank=True,
                choices=[
                    ("LF", "LF"),
                    ("PG", "PG"),
                    ("AF", "AF"),
                    ("JA", "JA"),
                    ("NAVY", "Navy"),
                    ("OTHER", "Other"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="coursesession",
            name="service_branch_other",
            field=models.CharField(
                blank=True,
                default="",
                max_length=100,
            ),
        ),
    ]

