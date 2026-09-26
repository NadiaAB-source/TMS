"""Report the exact scope of a future historical-course reset.

This command is intentionally read-only. It is the mandatory first step before
any destructive reset, so the owner can approve a precise scope from real data.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from portal.models import (
    ActivityLog,
    Camp,
    Course,
    CourseInstructor,
    CourseInstructorInventoryUsage,
    CourseInventoryUsage,
    CourseRosterSnapshot,
    CourseRosterSnapshotItem,
    CourseSequenceCounter,
    CourseSession,
    CourseSessionProposal,
    DataIssue,
    Instructor,
    InstructorAllocation,
    InstructorInventoryMovement,
    Registration,
    SourceFile,
    SourceRecord,
    StampedListArchive,
    Student,
    StudentIdentityProposal,
    TrainingRecord,
)


def _storage_summary(root):
    root = Path(root)
    file_count = 0
    total_bytes = 0
    if not root.exists():
        return file_count, total_bytes
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        file_count += 1
        try:
            total_bytes += path.stat().st_size
        except OSError:
            continue
    return file_count, total_bytes


class Command(BaseCommand):
    help = (
        "Read-only audit of records and files that may be affected by the "
        "approved historical course-data reset."
    )

    def handle(self, *args, **options):
        roster_root = (settings.BASE_DIR.parent / "saved course lists").resolve()
        stamped_root = (settings.BASE_DIR.parent / "stamped lists").resolve()
        roster_files, roster_bytes = _storage_summary(roster_root)
        stamped_files, stamped_bytes = _storage_summary(stamped_root)
        course_activity_types = (
            "Course",
            "CourseSession",
            "CourseInstructor",
            "Registration",
            "TrainingRecord",
            "CourseRosterSnapshot",
            "StampedListArchive",
            "CourseInventoryUsage",
            "CourseInstructorInventoryUsage",
            "InstructorInventoryMovement",
            "InstructorAllocation",
        )
        counter = CourseSequenceCounter.objects.filter(
            key="course_session"
        ).values("enabled", "next_number").first()

        self.stdout.write(
            self.style.WARNING(
                "DRY RUN ONLY — no records, passwords, or files were changed."
            )
        )
        self.stdout.write("\nCore course history")
        for label, count in (
            ("Course sessions", CourseSession.objects.count()),
            ("Course catalogue records", Course.objects.count()),
            ("Instructor assignments", CourseInstructor.objects.count()),
            ("Registrations", Registration.objects.count()),
            ("Training records", TrainingRecord.objects.count()),
            ("Saved roster versions", CourseRosterSnapshot.objects.count()),
            ("Saved roster rows", CourseRosterSnapshotItem.objects.count()),
            ("Stamped-list versions", StampedListArchive.objects.count()),
            ("Legacy course inventory reports", CourseInventoryUsage.objects.count()),
            (
                "Instructor course inventory reports",
                CourseInstructorInventoryUsage.objects.count(),
            ),
            (
                "Session-linked inventory movements",
                InstructorInventoryMovement.objects.filter(session__isnull=False).count(),
            ),
            (
                "Session-linked J35 allocations",
                InstructorAllocation.objects.filter(session__isnull=False).count(),
            ),
            ("Course-session proposals", CourseSessionProposal.objects.count()),
        ):
            self.stdout.write(f"  {label}: {count}")

        self.stdout.write("\nPotentially related historical data (requires scope approval)")
        for label, count in (
            ("Master students", Student.objects.count()),
            (
                "All J35 allocations (including standalone planning)",
                InstructorAllocation.objects.count(),
            ),
            (
                "Source records linked to courses/sessions/students",
                SourceRecord.objects.filter(
                    Q(linked_course__isnull=False)
                    | Q(linked_session__isnull=False)
                    | Q(linked_student__isnull=False)
                    | Q(linked_registration__isnull=False)
                    | Q(linked_training_record__isnull=False)
                ).count(),
            ),
            ("All source records", SourceRecord.objects.count()),
            ("Source files", SourceFile.objects.count()),
            ("Data-quality issues", DataIssue.objects.count()),
            ("Student identity proposals", StudentIdentityProposal.objects.count()),
            (
                "Course-related activity-log entries",
                ActivityLog.objects.filter(object_type__in=course_activity_types).count(),
            ),
        ):
            self.stdout.write(f"  {label}: {count}")

        self.stdout.write("\nArchived files")
        self.stdout.write(
            f"  Saved Day 1 list files: {roster_files} ({roster_bytes:,} bytes)"
        )
        self.stdout.write(
            f"  Stamped-list files: {stamped_files} ({stamped_bytes:,} bytes)"
        )

        self.stdout.write("\nRecords explicitly preserved by a course reset")
        self.stdout.write(f"  Camps: {Camp.objects.count()}")
        self.stdout.write(f"  Staff: {Instructor.objects.count()}")
        if counter:
            self.stdout.write(
                "  Course numbering: "
                + ("enabled" if counter["enabled"] else "waiting for reset")
                + f"; next number {counter['next_number']}"
            )
        else:
            self.stdout.write("  Course numbering: migration not yet applied")

        self.stdout.write(
            self.style.WARNING(
                "\nSend this output before any deletion. The reset must separately "
                "confirm whether to remove master students, source history, and J35 "
                "allocations; those choices cannot be inferred safely."
            )
        )
