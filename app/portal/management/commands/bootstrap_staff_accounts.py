"""Create secure accounts and role groups for the existing Staff records."""

import csv
import os
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from portal.models import Instructor, UserSecurityProfile
from portal.normalization import normalize_email
from portal.staff_groups import (
    ensure_predefined_groups,
    ensure_predefined_staff_positions,
    synchronise_staff_groups,
)


class Command(BaseCommand):
    help = (
        "Create missing Staff accounts, assign least-privilege groups, and "
        "export one-time temporary passwords securely."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Perform the account and group changes. Without this flag the command is a dry run.",
        )
        parser.add_argument(
            "--export",
            help=(
                "Required with --apply. CSV path inside the project backups directory, "
                "for example ../backups/iqarus_initial_passwords.csv."
            ),
        )
        parser.add_argument(
            "--reset-existing-passwords",
            action="store_true",
            help="Also replace passwords for existing linked accounts. This is never the default.",
        )

    def _export_path(self, supplied_path):
        if not supplied_path:
            raise CommandError("--apply requires --export so no temporary password is lost.")
        backup_root = (settings.BASE_DIR.parent / "backups").resolve()
        path = Path(supplied_path).expanduser().resolve()
        if backup_root != path.parent and backup_root not in path.parents:
            raise CommandError(
                "The password export must stay inside the project backups directory."
            )
        if path.suffix.casefold() != ".csv":
            raise CommandError("Use a .csv filename for the password export.")
        backup_root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise CommandError(
                f"Refusing to overwrite the existing credentials export: {path}"
            )
        return path

    @staticmethod
    def _temporary_password():
        return secrets.token_urlsafe(24)

    @staticmethod
    def _username_base(instructor):
        email = normalize_email(instructor.email)
        local_part = email.split("@", 1)[0] if "@" in email else ""
        base = slugify(local_part).replace("-", "_")
        if not base:
            base = slugify(instructor.name_english).replace("-", "_")
        return base or f"staff_{instructor.pk}"

    @staticmethod
    def _unique_username(user_model, base):
        max_length = user_model._meta.get_field("username").max_length
        base = base[:max_length]
        candidate = base
        sequence = 2
        while user_model.objects.filter(username__iexact=candidate).exists():
            suffix = f"_{sequence}"
            candidate = f"{base[: max_length - len(suffix)]}{suffix}"
            sequence += 1
        return candidate

    @staticmethod
    def _staff_email(instructor):
        if instructor.email:
            return normalize_email(instructor.email)
        if instructor.user_id and instructor.user.email:
            return normalize_email(instructor.user.email)
        return ""

    @staticmethod
    def _write_credentials_temp(path, rows):
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        fd = None
        try:
            fd = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
                fd = None
                writer = csv.DictWriter(
                    handle,
                    fieldnames=(
                        "staff_name",
                        "email",
                        "username",
                        "temporary_password",
                        "permission_groups",
                    ),
                )
                writer.writeheader()
                writer.writerows(rows)
            return temporary_path
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            if fd is not None:
                os.close(fd)

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        export_argument = options["export"]
        reset_existing = options["reset_existing_passwords"]
        if export_argument and not apply_changes:
            raise CommandError("--export is only valid together with --apply.")

        staff = list(
            Instructor.objects.select_related("user").prefetch_related("roles").order_by(
                "name_english", "id"
            )
        )
        user_model = get_user_model()
        missing_email = [
            instructor
            for instructor in staff
            if not instructor.user_id and not self._staff_email(instructor)
        ]
        missing_accounts = [
            instructor for instructor in staff if not instructor.user_id
        ]

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only: no accounts, groups, passwords, or files were changed."
                )
            )
            self.stdout.write(
                f"Staff records: {len(staff)} | missing accounts: {len(missing_accounts)} | "
                f"cannot create without email: {len(missing_email)}"
            )
            if reset_existing:
                self.stdout.write(
                    self.style.WARNING(
                        "Existing passwords would also be reset when --apply is supplied."
                    )
                )
            return

        export_path = self._export_path(export_argument)
        credential_rows = []
        created_count = 0
        reset_count = 0
        skipped_count = 0
        temporary_path = export_path.with_suffix(export_path.suffix + ".tmp")

        try:
            with transaction.atomic():
                ensure_predefined_staff_positions()
                groups = ensure_predefined_groups()
                for instructor in staff:
                    email = self._staff_email(instructor)
                    if not instructor.user_id and not email:
                        skipped_count += 1
                        self.stderr.write(
                            self.style.WARNING(
                                f"Skipped {instructor.name_english}: no email address."
                            )
                        )
                        continue

                    user = instructor.user
                    temporary_password = ""
                    if user is None:
                        username = self._unique_username(
                            user_model,
                            self._username_base(instructor),
                        )
                        temporary_password = self._temporary_password()
                        user = user_model.objects.create_user(
                            username=username,
                            email=email,
                            password=temporary_password,
                        )
                        instructor.user = user
                        instructor.email = email
                        instructor.save(update_fields=["user", "email", "updated_at"])
                        UserSecurityProfile.objects.update_or_create(
                            user=user,
                            defaults={
                                "must_change_password": True,
                                "temporary_password_issued_at": timezone.now(),
                                "password_changed_at": None,
                            },
                        )
                        created_count += 1
                    elif reset_existing:
                        temporary_password = self._temporary_password()
                        user.email = email
                        user.set_password(temporary_password)
                        user.save(update_fields=["email", "password"])
                        UserSecurityProfile.objects.update_or_create(
                            user=user,
                            defaults={
                                "must_change_password": True,
                                "temporary_password_issued_at": timezone.now(),
                                "password_changed_at": None,
                            },
                        )
                        reset_count += 1

                    if instructor.user_id and email and instructor.email != email:
                        instructor.email = email
                        instructor.save(update_fields=["email", "updated_at"])
                    wanted_groups = synchronise_staff_groups(instructor, groups)
                    if temporary_password:
                        credential_rows.append(
                            {
                                "staff_name": instructor.name_english,
                                "email": email,
                                "username": user.username,
                                "temporary_password": temporary_password,
                                "permission_groups": "; ".join(
                                    sorted(wanted_groups)
                                ),
                            }
                        )

                # Writing the temporary file before commit lets a write failure
                # roll back account changes. It is published only after commit.
                self._write_credentials_temp(export_path, credential_rows)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        try:
            os.replace(temporary_path, export_path)
            try:
                os.chmod(export_path, 0o600)
            except OSError:
                self.stderr.write(
                    self.style.WARNING(
                        "The mounted storage does not support POSIX permissions; "
                        "protect the credentials file through Drive sharing controls."
                    )
                )
        except Exception as exc:
            raise CommandError(
                "Accounts were committed, but the credential file could not be published. "
                f"Recover it from {temporary_path}: {exc}"
            ) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {created_count} account(s), reset {reset_count} account(s), "
                f"and assigned predefined groups."
            )
        )
        if skipped_count:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {skipped_count} staff record(s) with no email address."
                )
            )
        self.stdout.write(
            self.style.WARNING(
                f"Temporary credentials written to {export_path}. Keep this file private and remove it after distribution."
            )
        )
