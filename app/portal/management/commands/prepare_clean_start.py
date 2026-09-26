import json
import os
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from portal.clean_start_service import reset_copy


class Command(BaseCommand):
    help = 'Create the approved zero-course/student state in an isolated installer staging copy only.'

    def add_arguments(self, parser):
        parser.add_argument('--admin', required=True)

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR).resolve()
        db = Path(connection.settings_dict['NAME']).resolve()
        marker = base.parent / '.clean_start_staging.json'
        if not marker.is_file() or connection.vendor != 'sqlite':
            raise CommandError('Reset is available only in a marked SQLite staging copy.')
        record = json.loads(marker.read_text())
        if record.get('token') != os.environ.get('IQARUS_CLEAN_START_TOKEN') or record.get('app') != str(base) or db != base / 'db.sqlite3':
            raise CommandError('Staging/database protection check failed. Nothing reset.')
        result = reset_copy(options['admin'])
        (base.parent / 'clean_start_report.json').write_text(json.dumps(result, indent=2))
        self.stdout.write(json.dumps(result, indent=2))
