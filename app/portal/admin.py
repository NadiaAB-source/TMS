from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

from .identity import format_emirates_id
from .course_services import allocate_course_sequence_number
from .models import (
    ActivityLog,
    Camp,
    CampContact,
    Course,
    CourseInstructor,
    CourseInstructorInventoryUsage,
    CourseInventoryUsage,
    CourseRosterSnapshot,
    CourseRosterSnapshotItem,
    CourseSession,
    CourseSessionProposal,
    DataIssue,
    Instructor,
    InstructorAllocation,
    InstructorInventoryBalance,
    InstructorInventoryMovement,
    InventoryItem,
    EntityAliasProposal,
    InstructorRole,
    J35TrainingNeed,
    LoginAttempt,
    Registration,
    SourceFile,
    SourceRecord,
    Student,
    StudentIdentityProposal,
    Team,
    TrainingRecord,
    StampedListArchive,
    UserSecurityProfile,
    StaffPermissionOverride,
    J35GridCell,
    J35GridCourseLink,
    WarehouseStockPolicy,
    InventoryIssue,
    InventoryIssueLine,
)


admin.site.site_header = "IQARUS TMS Administration"
admin.site.site_title = "IQARUS TMS"
admin.site.index_title = "Training Management Control Panel"


class WorkflowRecordAdmin(admin.ModelAdmin):
    """Review records here; write through the validated, audited module forms."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StaffPermissionOverride)
class StaffPermissionOverrideAdmin(WorkflowRecordAdmin):
    list_display = ("user", "feature", "level", "updated_by", "updated_at")
    list_filter = ("feature", "level")
    search_fields = ("user__username", "user__email")


@admin.register(InventoryIssue)
class InventoryIssueAdmin(WorkflowRecordAdmin):
    list_display = ("public_id", "instructor", "issued_on", "created_by", "reversed_at")
    list_filter = ("issued_on",)
    search_fields = ("instructor__name_english", "notes")


admin.site.register(J35GridCell, WorkflowRecordAdmin)
admin.site.register(J35GridCourseLink, WorkflowRecordAdmin)
admin.site.register(WarehouseStockPolicy, WorkflowRecordAdmin)
admin.site.register(InventoryIssueLine, WorkflowRecordAdmin)


@admin.register(SourceFile)
class SourceFileAdmin(admin.ModelAdmin):
    list_display = ("filename", "records_found", "imported", "created_at")
    search_fields = ("filename", "sha256")
    list_filter = ("imported",)
    readonly_fields = ("sha256", "size_bytes", "created_at", "updated_at")
    list_per_page = 50


@admin.register(DataIssue)
class DataIssueAdmin(admin.ModelAdmin):
    list_display = (
        "summary",
        "category",
        "status",
        "source_file",
        "source_row",
    )
    search_fields = ("summary", "original_value", "decision_notes")
    list_filter = ("status", "category", "source_file")
    autocomplete_fields = ("source_file", "resolved_by")
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "formatted_eid",
        "email",
        "identity_status",
        "active",
    )
    search_fields = (
        "name_english_upper",
        "name_arabic",
        "eid",
        "email",
        "phone",
    )
    list_filter = ("identity_status", "active")
    readonly_fields = ("public_id", "created_at", "updated_at")
    list_per_page = 50

    @admin.display(description="Emirates ID", ordering="eid")
    def formatted_eid(self, obj):
        return format_emirates_id(obj.eid)


@admin.register(Camp)
class CampAdmin(admin.ModelAdmin):
    list_display = ("name", "area", "active")
    search_fields = ("name", "area")
    list_filter = ("active",)


@admin.register(CampContact)
class CampContactAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "camp",
        "position",
        "phone",
        "email",
        "is_primary",
        "active",
    )
    search_fields = ("name", "position", "phone", "email", "camp__name")
    list_filter = ("active", "is_primary", "camp")
    autocomplete_fields = ("camp",)
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("camp",)
    list_per_page = 50


@admin.register(J35TrainingNeed)
class J35TrainingNeedAdmin(admin.ModelAdmin):
    list_display = (
        "camp",
        "course",
        "source",
        "status",
        "priority",
        "requested_start_date",
        "requested_end_date",
        "required_instructors",
        "confirmed_at",
    )
    search_fields = (
        "camp__name",
        "contact__name",
        "course__title_english",
        "notes",
        "contact_notes",
    )
    list_filter = ("source", "status", "priority", "camp", "preferred_team")
    autocomplete_fields = (
        "camp",
        "contact",
        "course",
        "preferred_team",
        "required_role",
        "proposed_session",
        "created_by",
        "updated_by",
        "confirmed_by",
    )
    readonly_fields = ("public_id", "created_at", "updated_at", "confirmed_at")
    list_select_related = (
        "camp",
        "contact",
        "course",
        "preferred_team",
        "required_role",
        "proposed_session",
        "confirmed_by",
    )
    list_per_page = 50


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "leader", "active")
    search_fields = ("name",)
    list_filter = ("active",)
    autocomplete_fields = ("leader",)


@admin.register(InstructorRole)
class InstructorRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "active")
    search_fields = ("name",)
    list_filter = ("active",)


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = (
        "name_english",
        "formatted_eid",
        "email",
        "user",
        "team",
        "supervisor",
        "is_inventory_supervisor",
        "active",
    )
    search_fields = (
        "name_english",
        "name_arabic",
        "eid",
        "email",
    )
    list_filter = ("active", "team", "roles", "is_inventory_supervisor")
    autocomplete_fields = ("user", "team", "supervisor")
    filter_horizontal = ("roles",)
    readonly_fields = ("public_id", "created_at", "updated_at")
    list_per_page = 50

    @admin.display(description="Emirates ID", ordering="eid")
    def formatted_eid(self, obj):
        return format_emirates_id(obj.eid)


@admin.register(InstructorAllocation)
class InstructorAllocationAdmin(admin.ModelAdmin):
    list_display = (
        "activity",
        "instructor",
        "allocation_kind",
        "training_need",
        "start_date",
        "end_date",
        "status",
        "camp",
        "session",
        "notified_at",
        "updated_by",
    )
    search_fields = (
        "activity",
        "instructor__name_english",
        "session__reference_code",
        "session__course__title_english",
    )
    list_filter = ("status", "allocation_kind", "camp", "start_date")
    autocomplete_fields = (
        "instructor",
        "session",
        "training_need",
        "camp",
        "created_by",
        "updated_by",
    )
    readonly_fields = ("public_id", "created_at", "updated_at")
    list_select_related = (
        "instructor",
        "session",
        "training_need",
        "camp",
        "updated_by",
    )
    list_per_page = 50


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = (
        "title_english",
        "code",
        "duration_days",
        "duration_hours",
        "active",
    )
    search_fields = ("title_english", "title_arabic", "code")
    list_filter = ("active",)
    readonly_fields = ("public_id", "created_at", "updated_at")
    list_per_page = 50


@admin.register(CourseSession)
class CourseSessionAdmin(admin.ModelAdmin):
    list_display = (
        "sequence_number",
        "course",
        "reference_code",
        "start_date",
        "end_date",
        "camp",
        "capacity",
        "instructor_student_ratio",
        "status",
        "registration_published",
    )
    search_fields = (
        "course__title_english",
        "course__title_arabic",
        "course__code",
        "reference_code",
    )
    list_filter = ("status", "registration_published", "camp")
    autocomplete_fields = ("course", "camp", "created_by")
    readonly_fields = (
        "public_id",
        "public_registration_token",
        "sequence_number",
        "created_at",
        "updated_at",
    )
    list_per_page = 50

    def save_model(self, request, obj, form, change):
        if not change and obj.sequence_number is None:
            obj.sequence_number = allocate_course_sequence_number()
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CourseInstructor)
class CourseInstructorAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "instructor",
        "assignment_role",
        "notified_at",
    )
    search_fields = (
        "session__course__title_english",
        "instructor__name_english",
        "instructor__name_arabic",
    )
    list_filter = ("assignment_role",)
    autocomplete_fields = ("session", "instructor")
    list_per_page = 50


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "unit", "quantity_on_hand", "active")
    search_fields = ("name", "notes")
    list_filter = ("category", "active")


@admin.register(CourseInventoryUsage)
class CourseInventoryUsageAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "item",
        "quantity_issued",
        "quantity_returned",
        "quantity_consumed",
        "recorded_by",
    )
    list_filter = ("item__category", "session__camp")
    autocomplete_fields = ("session", "item", "recorded_by")


@admin.register(InstructorInventoryBalance)
class InstructorInventoryBalanceAdmin(admin.ModelAdmin):
    list_display = (
        "instructor",
        "item",
        "quantity_on_hand",
        "updated_by",
        "updated_at",
    )
    search_fields = ("instructor__name_english", "item__name")
    list_filter = ("item__category", "instructor__team")
    autocomplete_fields = ("instructor", "item", "updated_by")
    readonly_fields = ("created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InstructorInventoryMovement)
class InstructorInventoryMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "instructor",
        "item",
        "movement_type",
        "quantity_change",
        "balance_after",
        "session",
    )
    search_fields = (
        "instructor__name_english",
        "item__name",
        "session__reference_code",
        "notes",
    )
    list_filter = ("movement_type", "item__category", "created_at")
    autocomplete_fields = ("instructor", "item", "session", "recorded_by")
    readonly_fields = (
        "instructor",
        "item",
        "session",
        "movement_type",
        "quantity_change",
        "balance_after",
        "recorded_by",
        "notes",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CourseInstructorInventoryUsage)
class CourseInstructorInventoryUsageAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "instructor",
        "item",
        "quantity_used",
        "quantity_consumed",
        "quantity_deteriorated",
        "recorded_by",
    )
    search_fields = (
        "session__reference_code",
        "instructor__name_english",
        "item__name",
        "notes",
    )
    list_filter = ("item__category", "session__camp")
    autocomplete_fields = ("session", "instructor", "item", "recorded_by")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class CourseRosterSnapshotItemInline(admin.TabularInline):
    model = CourseRosterSnapshotItem
    extra = 0
    can_delete = False
    readonly_fields = (
        "serial_number",
        "registration",
        "email",
        "formatted_eid",
        "name_english",
        "name_arabic",
    )

    @admin.display(description="Emirates ID")
    def formatted_eid(self, obj):
        return format_emirates_id(obj.eid)


@admin.register(CourseRosterSnapshot)
class CourseRosterSnapshotAdmin(admin.ModelAdmin):
    list_display = ("session", "version", "student_count", "created_by", "created_at")
    search_fields = ("session__reference_code", "sha256")
    readonly_fields = (
        "public_id",
        "session",
        "version",
        "stored_path",
        "sha256",
        "student_count",
        "created_by",
        "created_at",
    )
    inlines = (CourseRosterSnapshotItemInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StampedListArchive)
class StampedListArchiveAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "document_type",
        "original_name",
        "uploaded_by",
        "uploaded_at",
        "active",
    )
    search_fields = ("session__reference_code", "original_name", "sha256")
    list_filter = ("document_type", "active", "uploaded_at")
    readonly_fields = (
        "public_id",
        "session",
        "original_name",
        "stored_path",
        "content_type",
        "document_type",
        "size_bytes",
        "sha256",
        "uploaded_by",
        "uploaded_at",
        "active",
        "notes",
    )

    def has_add_permission(self, request):
        return False


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "formatted_eid",
        "email_raw",
        "requested_session",
        "status",
        "duplicate_review_status",
        "submitted_at",
    )
    search_fields = (
        "submitted_name_english",
        "submitted_name_arabic",
        "eid_raw",
        "eid_normalized",
        "email_raw",
    )
    list_filter = (
        "status",
        "duplicate_review_status",
        "requested_session",
        "source_file",
    )
    autocomplete_fields = (
        "requested_session",
        "student",
        "source_file",
    )
    readonly_fields = (
        "public_id",
        "raw_payload",
        "duplicate_reviewed_at",
        "created_at",
        "updated_at",
    )
    list_per_page = 50

    @admin.display(description="Emirates ID", ordering="eid_normalized")
    def formatted_eid(self, obj):
        return format_emirates_id(obj.eid_normalized or obj.eid_raw)


@admin.register(TrainingRecord)
class TrainingRecordAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "session",
        "attendance",
        "result",
        "record_status",
        "is_hp",
        "is_ttt",
        "duplicate_flag",
    )
    search_fields = (
        "student__name_english",
        "student__name_arabic",
        "student__eid",
        "session__course__title_english",
        "session__course__code",
    )
    list_filter = (
        "attendance",
        "result",
        "record_status",
        "is_hp",
        "is_ttt",
        "duplicate_flag",
        "session__course",
        "session__camp",
    )
    autocomplete_fields = ("student", "session", "source_file")
    readonly_fields = (
        "public_id",
        "raw_payload",
        "created_at",
        "updated_at",
    )
    list_select_related = ("student", "session", "session__course")
    list_per_page = 50


@admin.register(SourceRecord)
class SourceRecordAdmin(admin.ModelAdmin):
    list_display = (
        "source_file",
        "worksheet_name",
        "source_row_number",
        "record_type",
        "import_status",
        "flag_count",
    )
    search_fields = (
        "source_file__filename",
        "worksheet_name",
        "record_type",
    )
    list_filter = (
        "import_status",
        "record_type",
        "source_file",
    )
    readonly_fields = (
        "source_record_id",
        "source_file",
        "worksheet_name",
        "source_row_number",
        "record_type",
        "raw_payload",
        "derived_search_values",
        "validation_flags",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = (
        "linked_student",
        "linked_session",
        "linked_registration",
        "linked_training_record",
    )
    list_select_related = ("source_file",)
    list_per_page = 50

    @admin.display(description="Flags")
    def flag_count(self, obj):
        return len(obj.validation_flags or [])

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.action(
    description="Approve selected safe student proposals"
)
def approve_safe_student_proposals(
    modeladmin,
    request,
    queryset,
):
    approved_count = 0
    skipped_count = 0

    with transaction.atomic():
        proposals = queryset.select_for_update()

        for proposal in proposals:
            if proposal.proposal_status in [
                StudentIdentityProposal
                .ProposalStatus
                .REVIEW_REQUIRED,
                StudentIdentityProposal
                .ProposalStatus
                .REJECTED,
            ]:
                skipped_count += 1
                continue

            if proposal.approved_student_id:
                skipped_count += 1
                continue

            student, created = Student.objects.get_or_create(
                eid=proposal.normalized_eid,
                defaults={
                    "name_english":
                        proposal.proposed_name_english,
                    "name_arabic":
                        proposal.proposed_name_arabic,
                    "email":
                        proposal.proposed_email,
                    "identity_status":
                        Student.IdentityStatus.VERIFIED,
                    "is_ttt": bool(
                        proposal.source_types.get(
                            "ttt_candidate_evidence"
                        )
                    ),
                },
            )

            proposal.approved_student = student
            proposal.proposal_status = (
                StudentIdentityProposal
                .ProposalStatus
                .APPROVED
            )
            proposal.reviewed_by = request.user
            proposal.reviewed_at = timezone.now()
            proposal.save(
                update_fields=[
                    "approved_student",
                    "proposal_status",
                    "reviewed_by",
                    "reviewed_at",
                    "updated_at",
                ]
            )

            proposal.source_records.update(
                linked_student=student
            )

            approved_count += 1

    if approved_count:
        messages.success(
            request,
            f"{approved_count} student proposals approved.",
        )

    if skipped_count:
        messages.warning(
            request,
            f"{skipped_count} proposals were skipped because "
            "they require review, were rejected, or were "
            "already approved.",
        )


@admin.register(StudentIdentityProposal)
class StudentIdentityProposalAdmin(admin.ModelAdmin):
    list_display = (
        "formatted_eid",
        "proposed_name_english",
        "proposed_name_arabic",
        "proposed_email",
        "evidence_count",
        "proposal_status",
        "flag_summary",
    )
    search_fields = (
        "normalized_eid",
        "proposed_name_english",
        "proposed_name_arabic",
        "proposed_email",
    )
    list_filter = (
        "proposal_status",
        "evidence_count",
    )
    readonly_fields = (
        "candidate_id",
        "normalized_eid",
        "proposal_status",
        "evidence_count",
        "source_types",
        "english_name_values",
        "arabic_name_values",
        "email_values",
        "flags",
        "approved_student",
        "reviewed_by",
        "reviewed_at",
        "created_at",
        "updated_at",
    )
    exclude = ("source_records",)
    actions = (approve_safe_student_proposals,)
    list_per_page = 50

    @admin.display(description="Emirates ID", ordering="normalized_eid")
    def formatted_eid(self, obj):
        return format_emirates_id(obj.normalized_eid)

    @admin.display(description="Flags")
    def flag_summary(self, obj):
        return ", ".join(obj.flags) if obj.flags else "None"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.action(
    description="Approve selected safe course proposals"
)
def approve_safe_course_proposals(
    modeladmin,
    request,
    queryset,
):
    approved_count = 0
    skipped_count = 0

    with transaction.atomic():
        for proposal in queryset.select_for_update():
            unsafe = (
                proposal.flags
                or not proposal.proposed_course_id
                or not proposal.proposed_camp_id
                or not proposal.start_date
                or not proposal.end_date
                or proposal.proposal_status in [
                    CourseSessionProposal
                    .ProposalStatus
                    .REJECTED,
                    CourseSessionProposal
                    .ProposalStatus
                    .REVIEW_REQUIRED,
                ]
                or proposal.approved_session_id
            )

            if unsafe:
                skipped_count += 1
                continue

            session = CourseSession.objects.create(
                course=proposal.proposed_course,
                camp=proposal.proposed_camp,
                start_date=proposal.start_date,
                end_date=proposal.end_date,
                sequence_number=allocate_course_sequence_number(),
                status=CourseSession.Status.DRAFT,
                notes=(
                    "Created from approved historical proposal "
                    f"{proposal.candidate_id}. "
                    "No cross-source merge was performed."
                ),
            )

            proposal.approved_session = session
            proposal.proposal_status = (
                CourseSessionProposal
                .ProposalStatus
                .APPROVED
            )
            proposal.reviewed_by = request.user
            proposal.reviewed_at = timezone.now()
            proposal.save(
                update_fields=[
                    "approved_session",
                    "proposal_status",
                    "reviewed_by",
                    "reviewed_at",
                    "updated_at",
                ]
            )

            proposal.source_records.update(
                linked_session_id=session.pk,
                linked_course_id=proposal.proposed_course_id,
                linked_camp_id=proposal.proposed_camp_id,
            )

            approved_count += 1

    if approved_count:
        messages.success(
            request,
            f"{approved_count} course proposals approved.",
        )

    if skipped_count:
        messages.warning(
            request,
            f"{skipped_count} proposals were skipped because "
            "they require review, are incomplete, were rejected, "
            "or were already approved.",
        )


@admin.register(CourseSessionProposal)
class CourseSessionProposalAdmin(admin.ModelAdmin):
    list_display = (
        "start_date",
        "end_date",
        "proposal_source",
        "proposed_camp",
        "evidence_records",
        "student_id_values",
        "proposal_status",
        "flag_summary",
    )
    search_fields = (
        "proposed_course__title_english",
        "proposed_camp__name",
    )
    list_filter = (
        "proposal_status",
        "proposal_source",
        "proposed_camp",
        "start_date",
    )
    autocomplete_fields = (
        "proposed_course",
        "proposed_camp",
    )
    readonly_fields = (
        "candidate_id",
        "proposal_source",
        "proposal_status",
        "camp_values",
        "unit_values",
        "instructor_values",
        "evidence_records",
        "student_id_values",
        "flags",
        "approved_session",
        "reviewed_by",
        "reviewed_at",
        "created_at",
        "updated_at",
    )
    exclude = ("source_records",)
    actions = (approve_safe_course_proposals,)
    list_per_page = 50
    date_hierarchy = "start_date"

    @admin.display(description="Flags")
    def flag_summary(self, obj):
        return ", ".join(obj.flags) if obj.flags else "None"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "actor",
        "action",
        "object_type",
        "description",
    )
    search_fields = (
        "actor__username",
        "object_type",
        "object_id",
        "description",
    )
    list_filter = (
        "actor",
        "action",
        "object_type",
        "created_at",
    )
    readonly_fields = (
        "actor",
        "action",
        "object_type",
        "object_id",
        "description",
        "details",
        "ip_address",
        "created_at",
    )
    date_hierarchy = "created_at"
    list_select_related = ("actor",)
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserSecurityProfile)
class UserSecurityProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "must_change_password",
        "temporary_password_issued_at",
        "password_changed_at",
    )
    search_fields = ("user__username", "user__email")
    list_filter = ("must_change_password",)
    autocomplete_fields = ("user",)
    readonly_fields = (
        "user",
        "must_change_password",
        "temporary_password_issued_at",
        "password_changed_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "last_attempt_at",
        "failure_count",
        "window_started_at",
        "locked_until",
    )
    list_filter = ("locked_until",)
    readonly_fields = (
        "key_hash",
        "failure_count",
        "window_started_at",
        "last_attempt_at",
        "locked_until",
    )
    ordering = ("-last_attempt_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.action(description="Approve selected alias proposals")
def approve_selected_aliases(
    modeladmin,
    request,
    queryset,
):
    approved_count = 0
    skipped_count = 0

    with transaction.atomic():
        for proposal in queryset.select_for_update():
            has_target = (
                proposal.proposed_camp_id
                if proposal.alias_type
                == EntityAliasProposal.AliasType.CAMP
                else proposal.proposed_instructor_id
            )

            if (
                not has_target
                or proposal.proposal_status
                == EntityAliasProposal
                .ProposalStatus
                .APPROVED
            ):
                skipped_count += 1
                continue

            proposal.proposal_status = (
                EntityAliasProposal
                .ProposalStatus
                .APPROVED
            )
            proposal.reviewed_by = request.user
            proposal.reviewed_at = timezone.now()
            proposal.save(
                update_fields=[
                    "proposal_status",
                    "reviewed_by",
                    "reviewed_at",
                    "updated_at",
                ]
            )
            approved_count += 1

        if approved_count:
            ActivityLog.objects.create(
                actor=request.user,
                action=ActivityLog.Action.APPROVE,
                object_type="EntityAliasProposal",
                object_id="batch",
                description=(
                    f"Approved {approved_count} alias proposals"
                ),
                details={
                    "approved_count": approved_count,
                    "skipped_count": skipped_count,
                },
            )

    if approved_count:
        messages.success(
            request,
            f"{approved_count} aliases approved.",
        )

    if skipped_count:
        messages.warning(
            request,
            f"{skipped_count} aliases were skipped.",
        )


@admin.register(EntityAliasProposal)
class EntityAliasProposalAdmin(admin.ModelAdmin):
    list_display = (
        "alias_type",
        "source_value",
        "target_display",
        "similarity_score",
        "match_method",
        "occurrences",
        "proposal_status",
    )
    search_fields = (
        "source_value",
        "normalized_source",
        "proposed_camp__name",
        "proposed_instructor__name_english",
    )
    list_filter = (
        "alias_type",
        "proposal_status",
        "match_method",
    )
    autocomplete_fields = (
        "proposed_camp",
        "proposed_instructor",
    )
    readonly_fields = (
        "candidate_id",
        "alias_type",
        "source_value",
        "normalized_source",
        "similarity_score",
        "match_method",
        "occurrences",
        "proposal_status",
        "reviewed_by",
        "reviewed_at",
        "created_at",
        "updated_at",
    )
    exclude = ("source_records",)
    actions = (approve_selected_aliases,)
    list_per_page = 50

    @admin.display(description="Proposed target")
    def target_display(self, obj):
        return obj.proposed_target or "Unresolved"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
