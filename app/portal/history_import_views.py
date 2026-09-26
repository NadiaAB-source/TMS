from pathlib import Path
import csv
import hashlib
import json
import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .history_import_schema import read_upload, ImportProblem, MAX_UPLOAD
from .history_import_service import apply_import, permitted
from .models import Camp, Instructor, InventoryItem


@login_required
@require_http_methods(['GET', 'POST'])
def historical_import(request):
    if not permitted(request.user):
        raise PermissionDenied('Historical import requires administrator access to courses and reports.')
    if request.method == 'GET' and request.GET.get('download') == 'example':
        path = Path(__file__).parent / 'resources' / 'Historical_Import_Example.xlsx'
        return FileResponse(path.open('rb'), as_attachment=True, filename=path.name)
    if request.method == 'GET' and request.GET.get('download') == 'names':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="IQARUS_import_names.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['type', 'value_for_import', 'description'])
        def safe(value):
            value = str(value)
            return "'" + value if value.startswith(('=', '+', '-', '@')) else value
        for camp in Camp.objects.order_by('name'):
            writer.writerow(['camp_name', safe(camp.name), ''])
        for staff in Instructor.objects.select_related('user').order_by('name_english'):
            writer.writerow(['username', safe(staff.user.username) if staff.user else '', safe(staff.name_english)])
        for item in InventoryItem.objects.order_by('name'):
            writer.writerow(['item_name', safe(item.name), safe(item.unit)])
        return response
    context = {}
    if request.method == 'POST':
        cache = Path(settings.BASE_DIR).parent / 'history_import_pending'
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            if request.POST.get('action') == 'apply':
                token = signing.loads(request.POST.get('token', ''), salt='iqarus-history-import', max_age=1800)
                if token.get('user') != request.user.pk or not isinstance(token.get('key'), str) or not __import__('re').fullmatch(r'[0-9a-f]{48}', token['key']):
                    raise ImportProblem('The preview belongs to another account or has expired.')
                path = cache / (token['key'] + '.upload')
                meta_path = cache / (token['key'] + '.json')
                if not path.is_file() or not meta_path.is_file():
                    raise ImportProblem('Upload the file again to create a new preview.')
                meta = json.loads(meta_path.read_text())
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != token['sha256'] or meta['user'] != request.user.pk:
                    raise ImportProblem('The uploaded file changed. Upload it again.')
                summary = apply_import(read_upload(data, meta['filename']), request.user, commit=True)
                path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                messages.success(request, summary['message'] if summary['already_imported'] else f"Imported {summary['courses']} courses and {summary['registrations']} registrations.")
                return redirect('historical_import')
            upload = request.FILES.get('history_file')
            if not upload or upload.size > MAX_UPLOAD:
                raise ImportProblem('Choose an Excel workbook or ZIP up to 100 MB.')
            data = upload.read(MAX_UPLOAD + 1)
            package = read_upload(data, upload.name)
            summary = apply_import(package, request.user, commit=False)
            if summary['already_imported']:
                context['summary'] = summary
            else:
                key = secrets.token_hex(24)
                path = cache / (key + '.upload')
                path.write_bytes(data)
                path.chmod(0o600)
                (cache / (key + '.json')).write_text(json.dumps({'user': request.user.pk, 'filename': package['filename']}))
                context.update(summary=summary, token=signing.dumps({'key': key, 'user': request.user.pk, 'sha256': package['sha256']}, salt='iqarus-history-import'))
        except (ImportProblem, ValidationError, signing.BadSignature) as exc:
            context['error'] = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        # Delete only stale preview files in this dedicated private directory.
        for p in cache.glob('*'):
            if p.is_file() and not p.is_symlink() and p.suffix in ('.json', '.upload') and time.time() - p.stat().st_mtime > 86400:
                p.unlink(missing_ok=True)
    return render(request, 'portal/history_import.html', context)
