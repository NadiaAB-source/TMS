"""Refresh IQARUS-managed permissions for the existing Staff accounts."""

from django.db import transaction
from django.core.management.base import BaseCommand

from portal.models import Instructor
from portal.staff_groups import (
    MANAGED_GROUP_NAMES,
    OPERATIONS_ADMIN_GROUP,
    ensure_predefined_groups,
    ensure_predefined_staff_positions,
    group_names_for_instructor,
    synchronise_staff_groups,
)


class Command(BaseCommand):
    help = (
        "Synchronise existing Staff accounts with their least-privilege "
        "position groups. Runs as a dry run unless --apply is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply group, active-status, and staff-status changes.",
        )

    @staticmethod
    def _current_managed_groups(instructor):
        return set(
            instructor.user.groups.filter(
                name__in=MANAGED_GROUP_NAMES
            ).values_list("name", flat=True)
        )

    @staticmethod
    def _would_change(instructor, wanted_groups):
        user = instructor.user
        expected_staff = user.is_superuser or (
            OPERATIONS_ADMIN_GROUP in wanted_groups
        )
        return (
            Command._current_managed_groups(instructor) != wanted_groups
            or user.is_active != instructor.active
            or user.is_staff != expected_staff
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        staff = list(
            Instructor.objects.select_related("user")
            .prefetch_related("roles")
            .order_by("name_english", "pk")
        )
        linked_staff = [instructor for instructor in staff if instructor.user_id]
        missing_accounts = len(staff) - len(linked_staff)
        changes = [
            (instructor, group_names_for_instructor(instructor))
            for instructor in linked_staff
        ]
        pending = [
            (instructor, wanted)
            for instructor, wanted in changes
            if self._would_change(instructor, wanted)
        ]

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only: no Staff accounts, group memberships, or "
                    "permissions were changed."
                )
            )
            self.stdout.write(
                f"Linked Staff accounts: {len(linked_staff)} | "
                f"without an account: {missing_accounts} | "
                f"accounts needing sync: {len(pending)}"
            )
            for instructor, wanted in pending:
                self.stdout.write(
                    f"Would synchronise {instructor.name_english}: "
                    f"{', '.join(sorted(wanted))}"
                )
            return

        with transaction.atomic():
            ensure_predefined_staff_positions()
            groups = ensure_predefined_groups()
            for instructor, _ in pending:
                synchronise_staff_groups(instructor, groups)

        self.stdout.write(
            self.style.SUCCESS(
                f"Refreshed predefined groups and synchronised "
                f"{len(pending)} of {len(linked_staff)} linked Staff account(s)."
            )
        )
        if missing_accounts:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {missing_accounts} Staff record(s) without a user account."
                )
            )
