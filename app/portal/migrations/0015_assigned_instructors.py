from django.db import migrations, models


def use_neutral_instructor_role(apps, schema_editor):
    CourseInstructor = apps.get_model("portal", "CourseInstructor")
    CourseInstructor.objects.filter(
        assignment_role__in=["lead", "assistant"]
    ).update(assignment_role="instructor")


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0014_clean_course_workflow"),
    ]

    operations = [
        migrations.RunPython(
            use_neutral_instructor_role,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="courseinstructor",
            name="assignment_role",
            field=models.CharField(
                choices=[
                    ("instructor", "Instructor"),
                    ("observer", "Observer"),
                    ("coordinator", "Coordinator"),
                ],
                default="instructor",
                max_length=20,
            ),
        ),
    ]

