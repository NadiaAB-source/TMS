import json
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from portal.history_import_schema import read_upload, ImportProblem
from portal.history_import_service import apply_import


class Command(BaseCommand):
    help = 'Validate an annotated historical workbook/ZIP; use --apply to commit the whole batch.'

    def add_arguments(self, parser):
        parser.add_argument('file')
        parser.add_argument('--admin', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        path = Path(options['file'])
        try:
            actor = get_user_model().objects.get(username=options['admin'])
            package = read_upload(path.read_bytes(), path.name)
            summary = apply_import(package, actor, commit=options['apply'])
        except (ImportProblem, OSError, get_user_model().DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(('IMPORTED\n' if options['apply'] else 'PREVIEW ONLY — no records saved\n') + json.dumps(summary, indent=2))
