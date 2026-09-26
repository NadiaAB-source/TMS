import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .normalization import normalize_email


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SourceFile(TimeStampedModel):
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, db_index=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    records_found = models.PositiveIntegerField(default=0)
    imported = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["filename"]

    def __str__(self):
        return self.filename


class DataIssue(TimeStampedModel):
    class Category(models.TextChoices):
        INVALID_ID = "invalid_id", "Invalid ID"
        IDENTITY_CONFLICT = "identity_conflict", "Identity conflict"
        DATE_CONFLICT = "date_conflict", "Date conflict"
        DUPLICATE = "duplicate", "Possible duplicate"
        SOURCE_MISMATCH = "source_mismatch", "Source mismatch"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWING = "reviewing", "Reviewing"
        RESOLVED = "resolved", "Resolved"
        ACCEPTED_AS_IS = "accepted_as_is", "Accepted as-is"

    source_file = models.ForeignKey(
        SourceFile,
        on_delete=models.PROTECT,
        related_name="issues",
    )
    source_sheet = models.CharField(max_length=150, blank=True)
    source_row = models.PositiveIntegerField(null=True, blank=True)
    category = models.CharField(max_length=40, choices=Category.choices)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.OPEN,
    )
    summary = models.CharField(max_length=255)
    original_value = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    decision_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_data_issues",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.summary


class Student(TimeStampedModel):
    class IdentityStatus(models.TextChoices):
        VERIFIED = "verified", "Verified"
        REVIEW = "review", "Needs review"
        INVALID_ID = "invalid_id", "Invalid ID"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    eid = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
    )
    name_english = models.CharField(max_length=255, blank=True)
    name_arabic = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    identity_status = models.CharField(
        max_length=20,
        choices=IdentityStatus.choices,
        default=IdentityStatus.REVIEW,
    )
    is_hp = models.BooleanField(default=False)
    is_ttt = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name_english", "name_arabic", "eid"]
        constraints = [
            models.UniqueConstraint(
                fields=["eid"],
                condition=Q(eid__isnull=False) & ~Q(eid=""),
                name="unique_student_eid_when_present",
            )
        ]
        indexes = [
            models.Index(fields=["name_english"]),
            models.Index(fields=["name_arabic"]),
            models.Index(fields=["email"]),
        ]

    @property
    def display_name(self):
        return (
            self.name_english.upper()
            if self.name_english
            else self.name_arabic or self.eid or "Student"
        )

    def name_english_upper(self):
        """Uppercase presentation without changing the stored name."""
        return self.name_english.upper()

    name_english_upper.short_description = "English name"
    name_english_upper.admin_order_field = "name_english"

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)


class Camp(TimeStampedModel):
    name = models.CharField(max_length=200, unique=True)
    area = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CampContact(TimeStampedModel):
    """A named point of contact at a camp used by the J35 planning pipeline."""

    camp = models.ForeignKey(
        Camp,
        on_delete=models.CASCADE,
        related_name="contacts",
    )
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    position = models.CharField(max_length=150, blank=True)
    active = models.BooleanField(default=True)
    is_primary = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["camp__name", "-is_primary", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["camp", "name"],
                name="unique_camp_contact_name",
            ),
            models.UniqueConstraint(
                fields=["camp"],
                condition=Q(is_primary=True, active=True),
                name="unique_primary_camp_contact",
            ),
        ]
        indexes = [
            models.Index(
                fields=["camp", "active", "is_primary"],
                name="portal_campcontact_active_idx",
            ),
        ]

    def __str__(self):
        return f"{self.name} — {self.camp}"

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)


class Team(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    leader = models.ForeignKey(
        "Instructor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="led_teams",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class InstructorRole(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Staff position"
        verbose_name_plural = "Staff positions"

    def __str__(self):
        return self.name


class Instructor(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instructor_profile",
    )
    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    eid = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
    )
    name_english = models.CharField(max_length=255)
    name_arabic = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    team = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="instructors",
    )
    roles = models.ManyToManyField(
        InstructorRole,
        blank=True,
        related_name="instructors",
    )
    supervisor = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="direct_reports",
    )
    active = models.BooleanField(default=True)
    is_inventory_supervisor = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name_english"]
        verbose_name = "Staff member"
        verbose_name_plural = "Staff"
        constraints = [
            models.UniqueConstraint(
                fields=["eid"],
                condition=Q(eid__isnull=False) & ~Q(eid=""),
                name="unique_instructor_eid_when_present",
            )
        ]

    def __str__(self):
        return self.name_english

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)


class Course(TimeStampedModel):
    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    code = models.CharField(
        max_length=80,
        null=True,
        blank=True,
        db_index=True,
    )
    title_english = models.CharField(max_length=255)
    title_arabic = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    duration_days = models.PositiveIntegerField(null=True, blank=True)
    duration_hours = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
    )
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["title_english"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=Q(code__isnull=False) & ~Q(code=""),
                name="unique_course_code_when_present",
            )
        ]

    def __str__(self):
        if self.code:
            return f"{self.code} — {self.title_english}"
        return self.title_english


class CourseSequenceCounter(models.Model):
    """Single locked counter for the visible course-session sequence.

    The counter deliberately belongs to course *sessions*, rather than the
    reusable course catalogue.  That keeps historic imports and every future
    course occurrence in one unambiguous sequence.
    """

    key = models.CharField(
        max_length=50,
        primary_key=True,
        default="course_session",
        editable=False,
    )
    next_number = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(
        default=False,
        help_text=(
            "Enable only after the approved historical course reset. Until then "
            "existing and newly created sessions keep their current display."
        ),
    )

    class Meta:
        verbose_name = "course sequence counter"
        verbose_name_plural = "course sequence counters"

    def __str__(self):
        return f"Next course number: {self.next_number}"


class CourseSession(TimeStampedModel):
    def save(self, *args, **kwargs):
        from .chronological_numbering import save_session
        return save_session(self, super().save, args, kwargs)

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REGISTRATION_OPEN = "registration_open", "Registration open"
        REGISTRATION_CLOSED = "registration_closed", "Registration closed"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class ServiceBranch(models.TextChoices):
        LF = "LF", "LF"
        PG = "PG", "PG"
        AF = "AF", "AF"
        JA = "JA", "JA"
        NAVY = "NAVY", "Navy"
        OTHER = "OTHER", "Other"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    reference_code = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
    )
    sequence_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text=(
            "Permanent chronological course-session number. Existing records "
            "remain unnumbered until the planned historical reset and import."
        ),
    )
    start_date = models.DateField(null=True, blank=True, db_index=True)
    end_date = models.DateField(null=True, blank=True, db_index=True)
    camp = models.ForeignKey(
        Camp,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="course_sessions",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    instructor_student_ratio = models.PositiveIntegerField(
        default=10,
        help_text="Expected students per instructor.",
    )
    service_branch = models.CharField(
        max_length=20,
        choices=ServiceBranch.choices,
        blank=True,
        default="",
    )
    service_branch_other = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )
    poc_name = models.CharField(max_length=200, blank=True)
    poc_contact_number = models.CharField(max_length=80, blank=True)
    poc_location_url = models.URLField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_course_sessions",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    course_information_sent_at = models.DateTimeField(null=True, blank=True)
    external_upload_generated_at = models.DateTimeField(null=True, blank=True)
    external_upload_confirmed_at = models.DateTimeField(null=True, blank=True)
    external_upload_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="confirmed_external_course_uploads",
    )
    public_registration_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    registration_published = models.BooleanField(default=False)
    instructors = models.ManyToManyField(
        Instructor,
        through="CourseInstructor",
        related_name="course_sessions",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "course__title_english"]
        indexes = [
            models.Index(fields=["status", "start_date"]),
            models.Index(fields=["camp", "start_date"]),
        ]

    def __str__(self):
        date = self.start_date.isoformat() if self.start_date else "Date pending"
        return f"{self.course} — {date}"

    @property
    def service_branch_name(self):
        if self.service_branch == self.ServiceBranch.OTHER:
            return self.service_branch_other or "Other"
        if self.service_branch:
            return self.get_service_branch_display()
        return "Not selected"


class CourseInstructor(TimeStampedModel):
    class AssignmentRole(models.TextChoices):
        INSTRUCTOR = "instructor", "Instructor"
        OBSERVER = "observer", "Observer"
        COORDINATOR = "coordinator", "Coordinator"

    session = models.ForeignKey(
        CourseSession,
        on_delete=models.CASCADE,
        related_name="instructor_assignments",
    )
    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.PROTECT,
        related_name="course_assignments",
    )
    assignment_role = models.CharField(
        max_length=20,
        choices=AssignmentRole.choices,
        default=AssignmentRole.INSTRUCTOR,
    )
    notes = models.TextField(blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["session", "assignment_role", "instructor"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "instructor"],
                name="unique_session_instructor",
            )
        ]

    def __str__(self):
        return f"{self.instructor} — {self.get_assignment_role_display()}"


class J35TrainingNeed(TimeStampedModel):
    """A camp training request or outreach opportunity awaiting planning."""

    class Source(models.TextChoices):
        CLIENT_REQUEST = "request", "Client request"
        OUTREACH = "outreach", "Proactive outreach"

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        REQUESTED = "requested", "Requested"
        PROPOSED = "proposed", "Proposed"
        CONFIRMED = "confirmed", "Confirmed"
        DEFERRED = "deferred", "Deferred"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    camp = models.ForeignKey(
        Camp,
        on_delete=models.PROTECT,
        related_name="j35_training_needs",
    )
    contact = models.ForeignKey(
        CampContact,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="training_needs",
    )
    course = models.ForeignKey(
        Course,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="j35_training_needs",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.OUTREACH,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    priority = models.CharField(
        max_length=12,
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )
    contacted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    requested_start_date = models.DateField(null=True, blank=True, db_index=True)
    requested_end_date = models.DateField(null=True, blank=True)
    expected_capacity = models.PositiveIntegerField(null=True, blank=True)
    required_instructors = models.PositiveSmallIntegerField(default=1)
    preferred_team = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="preferred_j35_training_needs",
    )
    required_role = models.ForeignKey(
        InstructorRole,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="required_j35_training_needs",
    )
    notes = models.TextField(blank=True)
    contact_notes = models.TextField(blank=True)
    proposed_session = models.ForeignKey(
        CourseSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="j35_training_needs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_j35_training_needs",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_j35_training_needs",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="confirmed_j35_training_needs",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["requested_start_date", "-created_at"]
        indexes = [
            models.Index(
                fields=["camp", "status", "requested_start_date"],
                name="portal_j35need_camp_st_dt_idx",
            ),
            models.Index(
                fields=["status", "requested_start_date"],
                name="portal_j35need_status_dt_idx",
            ),
            models.Index(
                fields=["camp", "contacted_at"],
                name="portal_j35need_contacted_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.requested_start_date
            and self.requested_end_date
            and self.requested_end_date < self.requested_start_date
        ):
            errors["requested_end_date"] = (
                "Requested end date cannot be before the requested start date."
            )
        if (
            self.contact_id
            and self.camp_id
            and self.contact.camp_id != self.camp_id
        ):
            errors["contact"] = "Select a contact from the selected camp."
        if (
            self.proposed_session_id
            and self.camp_id
            and self.proposed_session.camp_id
            and self.proposed_session.camp_id != self.camp_id
        ):
            errors["proposed_session"] = (
                "The proposed course must belong to the selected camp."
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        label = self.course or "Training need"
        return f"{self.camp} — {label}"


class InstructorAllocation(TimeStampedModel):
    """A planned J35 allocation, independent from the course workflow."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CANCELLED = "cancelled", "Cancelled"

    class AllocationKind(models.TextChoices):
        COURSE = "course", "Course"
        LEAVE = "leave", "Leave"
        ADMIN = "admin", "Administrative duty"
        OTHER = "other", "Other"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.PROTECT,
        related_name="j35_allocations",
    )
    session = models.ForeignKey(
        CourseSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="j35_allocations",
    )
    training_need = models.ForeignKey(
        J35TrainingNeed,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="allocations",
    )
    camp = models.ForeignKey(
        Camp,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="j35_allocations",
    )
    activity = models.CharField(max_length=200)
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    allocation_kind = models.CharField(
        max_length=20,
        choices=AllocationKind.choices,
        default=AllocationKind.OTHER,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    override_reason = models.TextField(blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_j35_allocations",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_j35_allocations",
    )

    class Meta:
        ordering = ["start_date", "end_date", "instructor__name_english"]
        indexes = [
            models.Index(
                fields=["instructor", "start_date", "end_date"],
                name="portal_j35_instr_dt_idx",
            ),
            models.Index(
                fields=["status", "start_date"],
                name="portal_j35_status_dt_idx",
            ),
            models.Index(
                fields=["training_need", "status"],
                name="portal_j35alloc_need_st_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError(
                {"end_date": "End date cannot be before the start date."}
            )
        if (
            self.training_need_id
            and self.camp_id
            and self.training_need.camp_id != self.camp_id
        ):
            raise ValidationError(
                {"camp": "The allocation camp must match its training need."}
            )
        if (
            self.instructor_id
            and self.start_date
            and self.end_date
            and self.status != self.Status.CANCELLED
        ):
            conflicts = InstructorAllocation.objects.filter(
                instructor_id=self.instructor_id,
                status__in=(self.Status.DRAFT, self.Status.PUBLISHED),
                start_date__lte=self.end_date,
                end_date__gte=self.start_date,
            )
            if self.pk:
                conflicts = conflicts.exclude(pk=self.pk)
            if conflicts.exists() and not self.override_reason.strip():
                raise ValidationError(
                    {
                        "instructor": (
                            "This instructor already has a J35 allocation "
                            "covering part of those dates. Record a manual "
                            "override reason before saving a deliberate overlap."
                        )
                    }
                )

    def __str__(self):
        return f"{self.instructor} — {self.activity} ({self.start_date:%d %b})"


class InventoryItem(TimeStampedModel):
    class Category(models.TextChoices):
        EQUIPMENT = "equipment", "Equipment"
        CONSUMABLE = "consumable", "Consumable"

    name = models.CharField(max_length=200, unique=True)
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.EQUIPMENT,
    )
    unit = models.CharField(max_length=50, default="item")
    quantity_on_hand = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class CourseInventoryUsage(TimeStampedModel):
    session = models.ForeignKey(
        CourseSession,
        on_delete=models.CASCADE,
        related_name="inventory_usage",
    )
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name="course_usage",
    )
    quantity_issued = models.PositiveIntegerField(default=0)
    quantity_returned = models.PositiveIntegerField(default=0)
    quantity_consumed = models.PositiveIntegerField(default=0)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recorded_inventory_usage",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "item"],
                name="unique_course_inventory_item",
            )
        ]

    def __str__(self):
        return f"{self.session} — {self.item}"


class InstructorInventoryBalance(TimeStampedModel):
    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.PROTECT,
        related_name="inventory_balances",
    )
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name="instructor_balances",
    )
    quantity_on_hand = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_instructor_inventory_balances",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["instructor__name_english", "item__category", "item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["instructor", "item"],
                name="unique_instructor_inventory_item",
            )
        ]

    def __str__(self):
        return f"{self.instructor} — {self.item}: {self.quantity_on_hand}"


class InstructorInventoryMovement(models.Model):
    class MovementType(models.TextChoices):
        ISSUE = "issue", "Initial issue"
        REFILL = "refill", "Refill"
        COURSE_REPORT = "course_report", "Course use report"
        CORRECTION = "correction", "Correction"

    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name="instructor_movements",
    )
    session = models.ForeignKey(
        CourseSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="instructor_inventory_movements",
    )
    movement_type = models.CharField(
        max_length=30,
        choices=MovementType.choices,
    )
    quantity_change = models.IntegerField()
    balance_after = models.PositiveIntegerField()
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recorded_instructor_inventory_movements",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["instructor", "created_at"]),
            models.Index(fields=["item", "created_at"]),
        ]

    def __str__(self):
        return (
            f"{self.instructor} — {self.item}: "
            f"{self.quantity_change:+d}"
        )


class CourseInstructorInventoryUsage(TimeStampedModel):
    session = models.ForeignKey(
        CourseSession,
        on_delete=models.CASCADE,
        related_name="instructor_inventory_usage",
    )
    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.PROTECT,
        related_name="course_inventory_usage",
    )
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name="instructor_course_usage",
    )
    quantity_used = models.PositiveIntegerField(default=0)
    quantity_consumed = models.PositiveIntegerField(default=0)
    quantity_deteriorated = models.PositiveIntegerField(default=0)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recorded_course_instructor_inventory_usage",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["instructor__name_english", "item__category", "item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "instructor", "item"],
                name="unique_course_instructor_inventory_item",
            )
        ]

    def __str__(self):
        return f"{self.session} — {self.instructor} — {self.item}"


class Registration(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        NEEDS_REVIEW = "needs_review", "Needs review"

    class DuplicateReviewStatus(models.TextChoices):
        CLEAR = "clear", "No duplicate found"
        REQUIRED = "review_required", "Review required"
        ALLOWED = "reviewed_allow", "Reviewed — allow"
        EXCLUDED = "reviewed_exclude", "Reviewed — exclude"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    requested_session = models.ForeignKey(
        CourseSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registrations",
    )
    student = models.ForeignKey(
        Student,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registrations",
    )
    submitted_name_english = models.CharField(max_length=255, blank=True)
    submitted_name_arabic = models.CharField(max_length=255, blank=True)
    eid_raw = models.CharField(max_length=100, blank=True)
    eid_normalized = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )
    email_raw = models.CharField(max_length=255, blank=True)
    phone_raw = models.CharField(max_length=100, blank=True)
    submitted_unit = models.CharField(max_length=255, blank=True)
    training_date_raw = models.CharField(max_length=150, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    source_file = models.ForeignKey(
        SourceFile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="registrations",
    )
    source_sheet = models.CharField(max_length=150, blank=True)
    source_row = models.PositiveIntegerField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    review_notes = models.TextField(blank=True)
    duplicate_review_status = models.CharField(
        max_length=24,
        choices=DuplicateReviewStatus.choices,
        default=DuplicateReviewStatus.CLEAR,
        db_index=True,
    )
    duplicate_review_notes = models.TextField(blank=True)
    duplicate_review_fingerprint = models.CharField(max_length=64, blank=True)
    duplicate_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_registration_duplicates",
    )
    duplicate_reviewed_at = models.DateTimeField(null=True, blank=True)
    selected_for_roster = models.BooleanField(default=False)
    day1_attended = models.BooleanField(default=False)
    day2_attended = models.BooleanField(default=False)
    is_hp = models.BooleanField(default=False)
    is_ttt = models.BooleanField(default=False)
    instructor_remarks = models.TextField(blank=True)
    assessment_status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("absent", "Absent"),
            ("incomplete", "Incomplete"),
        ],
        default="pending",
    )

    class Meta:
        ordering = ["-submitted_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "submitted_at"]),
        ]

    def __str__(self):
        return (
            self.submitted_name_english
            or self.submitted_name_arabic
            or self.eid_raw
            or str(self.public_id)
        )

    def save(self, *args, **kwargs):
        self.email_raw = normalize_email(self.email_raw)
        super().save(*args, **kwargs)


class CourseRosterSnapshot(models.Model):
    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    session = models.ForeignKey(
        CourseSession,
        on_delete=models.CASCADE,
        related_name="roster_snapshots",
    )
    version = models.PositiveIntegerField()
    stored_path = models.CharField(max_length=500, unique=True)
    sha256 = models.CharField(max_length=64)
    student_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_course_rosters",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "version"],
                name="unique_course_roster_version",
            )
        ]

    def __str__(self):
        return f"{self.session} — roster v{self.version}"


class CourseRosterSnapshotItem(models.Model):
    snapshot = models.ForeignKey(
        CourseRosterSnapshot,
        on_delete=models.CASCADE,
        related_name="items",
    )
    registration = models.ForeignKey(
        Registration,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="roster_snapshot_items",
    )
    serial_number = models.PositiveIntegerField()
    email = models.CharField(max_length=255, blank=True)
    eid = models.CharField(max_length=64, blank=True)
    name_english = models.CharField(max_length=255, blank=True)
    name_arabic = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["serial_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "serial_number"],
                name="unique_roster_snapshot_serial",
            )
        ]

    def __str__(self):
        return f"{self.snapshot} — {self.serial_number}"

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)


class TrainingRecord(TimeStampedModel):
    class Attendance(models.TextChoices):
        PENDING = "pending", "Pending"
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        PARTIAL = "partial", "Partial"
        NOT_RECORDED = "not_recorded", "Not recorded"

    class Result(models.TextChoices):
        PENDING = "pending", "Pending"
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        WITHDRAWN = "withdrawn", "Withdrawn"
        NOT_ASSESSED = "not_assessed", "Not assessed"

    class RecordStatus(models.TextChoices):
        REGISTERED = "registered", "Registered"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="training_records",
    )
    session = models.ForeignKey(
        CourseSession,
        on_delete=models.PROTECT,
        related_name="training_records",
    )
    attendance = models.CharField(
        max_length=20,
        choices=Attendance.choices,
        default=Attendance.PENDING,
    )
    assessment_score = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    result = models.CharField(
        max_length=20,
        choices=Result.choices,
        default=Result.PENDING,
    )
    record_status = models.CharField(
        max_length=20,
        choices=RecordStatus.choices,
        default=RecordStatus.REGISTERED,
    )
    is_hp = models.BooleanField(default=False)
    is_ttt = models.BooleanField(default=False)
    duplicate_flag = models.BooleanField(default=False, db_index=True)
    duplicate_notes = models.TextField(blank=True)
    source_file = models.ForeignKey(
        SourceFile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="training_records",
    )
    source_sheet = models.CharField(max_length=150, blank=True)
    source_row = models.PositiveIntegerField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-session__start_date", "student"]
        indexes = [
            models.Index(fields=["attendance", "result"]),
            models.Index(fields=["record_status"]),
            models.Index(fields=["is_hp", "is_ttt"]),
        ]

    def __str__(self):
        return f"{self.student} — {self.session}"


class SourceRecord(TimeStampedModel):
    class ImportStatus(models.TextChoices):
        STAGED = "staged", "Staged"
        NEEDS_REVIEW = "needs_review", "Needs review"
        CONVERTED = "converted", "Converted"
        IGNORED = "ignored", "Ignored after review"

    source_record_id = models.UUIDField(unique=True, editable=False)
    source_file = models.ForeignKey(
        SourceFile,
        on_delete=models.PROTECT,
        related_name="source_records",
    )
    worksheet_name = models.CharField(max_length=150)
    source_row_number = models.PositiveIntegerField()
    record_type = models.CharField(max_length=80, db_index=True)
    raw_payload = models.JSONField(default=dict)
    derived_search_values = models.JSONField(default=dict, blank=True)
    validation_flags = models.JSONField(default=list, blank=True)
    import_status = models.CharField(
        max_length=20,
        choices=ImportStatus.choices,
        default=ImportStatus.STAGED,
        db_index=True,
    )
    linked_camp = models.ForeignKey(
        Camp,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_evidence",
    )
    linked_instructor = models.ForeignKey(
        Instructor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_evidence",
    )
    linked_course = models.ForeignKey(
        Course,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_evidence",
    )

    linked_student = models.ForeignKey(
        Student,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_evidence",
    )
    linked_session = models.ForeignKey(
        CourseSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_evidence",
    )
    linked_registration = models.ForeignKey(
        Registration,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_evidence",
    )
    linked_training_record = models.ForeignKey(
        TrainingRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_evidence",
    )
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = [
            "source_file",
            "worksheet_name",
            "source_row_number",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "source_file",
                    "worksheet_name",
                    "source_row_number",
                    "record_type",
                ],
                name="unique_historical_source_record",
            )
        ]
        indexes = [
            models.Index(fields=["record_type", "import_status"]),
        ]

    def __str__(self):
        return (
            f"{self.source_file.filename} — "
            f"{self.worksheet_name} row {self.source_row_number}"
        )


class StudentIdentityProposal(TimeStampedModel):
    class ProposalStatus(models.TextChoices):
        SINGLE_VALID_ID = "single_valid_id", "Single valid ID"
        EXACT_ID_MATCH = "exact_id_match", "Exact-ID match"
        REVIEW_REQUIRED = "review_required", "Review required"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    candidate_id = models.UUIDField(unique=True, editable=False)
    normalized_eid = models.CharField(
        max_length=15,
        unique=True,
        db_index=True,
    )
    proposal_status = models.CharField(
        max_length=30,
        choices=ProposalStatus.choices,
    )
    proposed_name_english = models.CharField(
        max_length=255,
        blank=True,
    )
    proposed_name_arabic = models.CharField(
        max_length=255,
        blank=True,
    )
    proposed_email = models.CharField(max_length=255, blank=True)
    evidence_count = models.PositiveIntegerField(default=0)
    source_types = models.JSONField(default=dict, blank=True)
    english_name_values = models.JSONField(default=list, blank=True)
    arabic_name_values = models.JSONField(default=list, blank=True)
    email_values = models.JSONField(default=list, blank=True)
    flags = models.JSONField(default=list, blank=True)
    source_records = models.ManyToManyField(
        SourceRecord,
        blank=True,
        related_name="student_identity_proposals",
    )
    approved_student = models.OneToOneField(
        Student,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="identity_proposal",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_student_proposals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    owner_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["proposal_status", "normalized_eid"]
        indexes = [
            models.Index(fields=["proposal_status"]),
        ]

    def __str__(self):
        name = (
            self.proposed_name_english
            or self.proposed_name_arabic
            or "Unnamed"
        )
        return f"{self.normalized_eid} — {name}"

    def save(self, *args, **kwargs):
        self.proposed_email = normalize_email(self.proposed_email)
        super().save(*args, **kwargs)


class CourseSessionProposal(TimeStampedModel):
    class ProposalSource(models.TextChoices):
        REGISTRATION = (
            "registration_group",
            "Registration group",
        )
        TTT = (
            "ttt_evidence_group",
            "TTT evidence group",
        )
        SPECIALIZED = (
            "specialized_result_and_attendance",
            "Specialized result and attendance",
        )
        CAMP_SUMMARY = (
            "camp_operational_summary",
            "Camp operational summary",
        )

    class ProposalStatus(models.TextChoices):
        PENDING = "pending", "Pending review"
        REVIEW_REQUIRED = (
            "review_required",
            "Review required",
        )
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    candidate_id = models.UUIDField(unique=True, editable=False)
    proposal_source = models.CharField(
        max_length=50,
        choices=ProposalSource.choices,
        db_index=True,
    )
    proposal_status = models.CharField(
        max_length=30,
        choices=ProposalStatus.choices,
        default=ProposalStatus.PENDING,
        db_index=True,
    )
    proposed_course = models.ForeignKey(
        Course,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="session_proposals",
    )
    proposed_camp = models.ForeignKey(
        Camp,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="session_proposals",
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    camp_values = models.JSONField(default=list, blank=True)
    unit_values = models.JSONField(default=list, blank=True)
    instructor_values = models.JSONField(default=list, blank=True)
    evidence_records = models.PositiveIntegerField(default=0)
    student_id_values = models.PositiveIntegerField(default=0)
    flags = models.JSONField(default=list, blank=True)
    source_records = models.ManyToManyField(
        SourceRecord,
        blank=True,
        related_name="course_session_proposals",
    )
    approved_session = models.OneToOneField(
        CourseSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_proposal",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_course_proposals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    owner_notes = models.TextField(blank=True)

    class Meta:
        ordering = [
            "proposal_status",
            "-start_date",
            "proposal_source",
        ]
        indexes = [
            models.Index(
                fields=["proposal_source", "proposal_status"]
            ),
            models.Index(fields=["start_date", "end_date"]),
        ]

    def __str__(self):
        date = (
            self.start_date.isoformat()
            if self.start_date
            else "Date unresolved"
        )
        camp = (
            self.camp_values[0]
            if self.camp_values
            else "Camp unresolved"
        )
        return f"{date} — {camp}"


class ActivityLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"
        IMPORT = "import", "Import"
        EXPORT = "export", "Export"
        PRINT = "print", "Print"
        LOGIN = "login", "Login"
        CANCEL = "cancel", "Cancel"
        DELETE = "delete", "Delete"
        ISSUE = "issue", "Issue"
        REFILL = "refill", "Refill"
        OTHER = "other", "Other"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tms_activity",
    )
    action = models.CharField(
        max_length=20,
        choices=Action.choices,
        db_index=True,
    )
    object_type = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["object_type", "object_id"]
            ),
            models.Index(fields=["actor", "created_at"]),
            models.Index(
                fields=["action", "created_at"],
                name="portal_act_action_dt_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_action_display()} — {self.description}"


class UserSecurityProfile(TimeStampedModel):
    """Security state kept separate from Django's built-in User model."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tms_security_profile",
    )
    must_change_password = models.BooleanField(default=False, db_index=True)
    temporary_password_issued_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Security profile — {self.user}"


class LoginAttempt(models.Model):
    """Database-backed throttling state; identifiers are hashed, not stored."""

    key_hash = models.CharField(max_length=64, unique=True)
    failure_count = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField()
    last_attempt_at = models.DateTimeField()
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-last_attempt_at"]

    def __str__(self):
        return f"Login protection event at {self.last_attempt_at:%d %b %Y %H:%M}"


class Notification(models.Model):
    class Category(models.TextChoices):
        INFORMATION = "information", "Information"
        REVIEW = "review", "Review required"
        WARNING = "warning", "Warning"
        SUCCESS = "success", "Success"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tms_notifications",
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.INFORMATION,
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    link = models.CharField(max_length=500, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "read_at"]),
        ]

    @property
    def is_read(self):
        return self.read_at is not None

    def __str__(self):
        return self.title


class EntityAliasProposal(TimeStampedModel):
    class AliasType(models.TextChoices):
        CAMP = "camp", "Camp"
        INSTRUCTOR = "instructor", "Instructor"

    class ProposalStatus(models.TextChoices):
        PROPOSED = "proposed", "High-confidence proposal"
        REVIEW_REQUIRED = (
            "review_required",
            "Review required",
        )
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    candidate_id = models.UUIDField(unique=True, editable=False)
    alias_type = models.CharField(
        max_length=20,
        choices=AliasType.choices,
        db_index=True,
    )
    source_value = models.TextField()
    normalized_source = models.TextField(db_index=True)
    proposed_camp = models.ForeignKey(
        Camp,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="alias_proposals",
    )
    proposed_instructor = models.ForeignKey(
        Instructor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="alias_proposals",
    )
    similarity_score = models.DecimalField(
        max_digits=5,
        decimal_places=3,
        default=0,
    )
    match_method = models.CharField(max_length=80, blank=True)
    proposal_status = models.CharField(
        max_length=30,
        choices=ProposalStatus.choices,
        db_index=True,
    )
    occurrences = models.PositiveIntegerField(default=0)
    source_records = models.ManyToManyField(
        SourceRecord,
        blank=True,
        related_name="entity_alias_proposals",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_entity_aliases",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    owner_notes = models.TextField(blank=True)

    class Meta:
        ordering = [
            "alias_type",
            "proposal_status",
            "source_value",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["alias_type", "source_value"],
                name="unique_entity_alias_source_value",
            )
        ]
        indexes = [
            models.Index(
                fields=["alias_type", "proposal_status"]
            ),
        ]

    @property
    def proposed_target(self):
        return (
            self.proposed_camp
            if self.alias_type == self.AliasType.CAMP
            else self.proposed_instructor
        )

    def __str__(self):
        target = self.proposed_target
        return f"{self.source_value} → {target or 'Unresolved'}"


class StampedListArchive(models.Model):
    class DocumentType(models.TextChoices):
        STAMPED_LIST = "stamped_list", "Stamped List"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    session = models.ForeignKey(
        CourseSession,
        on_delete=models.CASCADE,
        related_name="stamped_list_archives",
    )
    original_name = models.CharField(max_length=255)
    stored_path = models.CharField(max_length=500, unique=True)
    content_type = models.CharField(max_length=100)
    document_type = models.CharField(
        max_length=20,
        choices=DocumentType.choices,
        default=DocumentType.STAMPED_LIST,
        db_index=True,
    )
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_stamped_lists",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        indexes = [
            models.Index(
                fields=["session", "active"],
                name="stamped_list_session_idx",
            ),
            models.Index(
                fields=["session", "document_type", "active"],
                name="portal_stamp_session_type_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.session}"


# Related feature models share the portal app and its migration history.
from .staff_access_models import StaffPermissionOverride  # noqa: E402,F401
from .j35_grid_models import J35GridCell, J35GridCourseLink  # noqa: E402,F401
from .inventory_models import (  # noqa: E402,F401
    WarehouseStockPolicy, InventoryIssue, InventoryIssueLine,
)
