from django.db import migrations


class Migration(migrations.Migration):
    """Keep inventory-index names within Django's portable identifier limit."""

    dependencies = [
        ("portal", "0020_course_sequence_and_unified_stamped_list"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="instructorinventorymovement",
            old_name="portal_iim_instr_created",
            new_name="portal_inst_instruc_75e789_idx",
        ),
        migrations.RenameIndex(
            model_name="instructorinventorymovement",
            old_name="portal_iim_item_created",
            new_name="portal_inst_item_id_5afb6b_idx",
        ),
    ]
