"""Compact staff directory and administrator-controlled permission matrix."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .access import can_edit_staff, can_view_staff, feature_permissions, role_feature_access
from .models import ActivityLog, Instructor, InstructorRole, Team, UserSecurityProfile
from .normalization import normalize_email
from .staff_access_models import STAFF_FEATURES, StaffPermissionOverride
from .staff_groups import synchronise_staff_groups


def _pk(value, *, optional=False):
    value = (value or "").strip()
    if not value and optional:
        return None
    if len(value) > 19 or not value.isdecimal():
        raise ValidationError("Choose a valid record from the list.")
    number = int(value)
    if number < 1 or number > 9223372036854775807:
        raise ValidationError("Choose a valid record from the list.")
    return number


def _team(data):
    pk = _pk(data.get("team_id"), optional=True)
    return get_object_or_404(Team, pk=pk) if pk else None


def _roles(data, instructor=None):
    ids = {_pk(value) for value in data.getlist("role_ids")}
    allowed = InstructorRole.objects.filter(active=True)
    if instructor:
        allowed = InstructorRole.objects.filter(Q(active=True) | Q(instructors=instructor)).distinct()
    roles = list(allowed.filter(pk__in=ids))
    if len(roles) != len(ids):
        raise ValidationError("Select active positions from the list.")
    return roles


def _contact(data):
    name = data.get("name", "").strip()
    email = normalize_email(data.get("email", ""))
    phone = data.get("phone", "").strip()
    if not name or len(name) > 255:
        raise ValidationError("Enter a full name of at most 255 characters.")
    if len(email) > 254:
        raise ValidationError("Email address is too long.")
    if email:
        validate_email(email)
    if len(phone) > 50:
        raise ValidationError("Mobile number is too long.")
    return name, email, phone


def _apply_permissions(request, instructor):
    if request.POST.get("permissions_present") != "yes":
        return
    submitted = {}
    for feature, _ in STAFF_FEATURES:
        level = request.POST.get("permission_" + feature, "")
        if level not in {"", "view", "edit"}:
            raise ValidationError("Choose Role default, View, or Edit for each permission.")
        submitted[feature] = level
    if not instructor.user_id:
        if any(submitted.values()):
            raise ValidationError("Create a linked user account before assigning permissions.")
        return
    if instructor.user.is_superuser:
        if any(submitted.values()):
            raise ValidationError("Superuser permissions are managed in Administration.")
        return
    for feature, level in submitted.items():
        if level:
            StaffPermissionOverride.objects.update_or_create(
                user=instructor.user, feature=feature,
                defaults={"level": level, "updated_by": request.user},
            )
        else:
            StaffPermissionOverride.objects.filter(user=instructor.user, feature=feature).delete()


def _audit(request, action, instance, description):
    # Deliberately whitelist metadata: form payloads may contain initial passwords.
    ActivityLog.objects.create(
        actor=request.user, action=action,
        object_type=instance.__class__.__name__, object_id=str(instance.pk),
        description=description,
        details={"staff_action": request.POST.get("action", "")},
    )


def _mutate(request):
    data = request.POST
    action = data.get("action", "")
    if action in {"add_team", "add_role"}:
        name = data.get("name", "").strip()
        if not name or len(name) > 150:
            raise ValidationError("Enter a name of at most 150 characters.")
        model = Team if action == "add_team" else InstructorRole
        instance = model.objects.create(name=name)
        _audit(request, ActivityLog.Action.CREATE, instance, "Staff directory entry created.")
    elif action == "add_instructor":
        name, email, phone = _contact(data)
        username = data.get("username", "").strip()
        password = data.get("temporary_password", "")
        if not email or not username or not password:
            raise ValidationError("Name, email, username and initial password are required.")
        User = get_user_model()
        candidate = User(username=username, email=email)
        candidate.full_clean(exclude=["password"])
        validate_password(password, user=candidate)
        roles = _roles(data)
        user = User.objects.create_user(username=username, email=email, password=password)
        instructor = Instructor.objects.create(user=user, name_english=name, email=email, phone=phone, team=_team(data))
        instructor.roles.set(roles)
        change_required = data.get("require_password_change") == "yes"
        UserSecurityProfile.objects.update_or_create(user=user, defaults={
            "must_change_password": change_required,
            "temporary_password_issued_at": timezone.now() if change_required else None,
        })
        synchronise_staff_groups(instructor)
        _apply_permissions(request, instructor)
        _audit(request, ActivityLog.Action.CREATE, instructor, "Staff account created.")
    elif action == "update_instructor":
        instructor = get_object_or_404(
            Instructor.objects.select_for_update(of=("self",)).select_related("user"),
            pk=_pk(data.get("instructor_id")),
        )
        if instructor.user_id and instructor.user.is_superuser and not request.user.is_superuser:
            raise PermissionDenied("Only a superuser may change a superuser staff account.")
        name, email, phone = _contact(data)
        roles = _roles(data, instructor)
        active = data.get("active") == "yes"
        if instructor.user_id == request.user.pk and not active:
            raise ValidationError("You cannot deactivate your own account.")
        instructor.name_english, instructor.email = name, email
        instructor.phone, instructor.team = phone, _team(data)
        instructor.active = active
        instructor.is_inventory_supervisor = data.get("is_inventory_supervisor") == "yes"
        instructor.save()
        instructor.roles.set(roles)
        if instructor.user_id:
            instructor.user.email = email
            instructor.user.save(update_fields=["email"])
        synchronise_staff_groups(instructor)
        _apply_permissions(request, instructor)
        _audit(request, ActivityLog.Action.UPDATE, instructor, "Staff contact, positions and permissions updated.")
    elif action == "set_team_leader":
        team = get_object_or_404(Team, pk=_pk(data.get("team_id")))
        leader_id = _pk(data.get("leader_id"), optional=True)
        leader = get_object_or_404(Instructor, pk=leader_id, active=True) if leader_id else None
        if leader and leader.team_id != team.pk:
            leader.team = team
            leader.save(update_fields=["team", "updated_at"])
        team.leader = leader
        team.save(update_fields=["leader", "updated_at"])
        _audit(request, ActivityLog.Action.UPDATE, team, "Team leader updated.")
    elif action in {"toggle_team", "toggle_role"}:
        model = Team if action == "toggle_team" else InstructorRole
        key = "team_id" if action == "toggle_team" else "role_id"
        instance = get_object_or_404(model, pk=_pk(data.get(key)))
        instance.active = not instance.active
        instance.save(update_fields=["active", "updated_at"])
        if action == "toggle_role":
            for instructor in instance.instructors.select_related("user").prefetch_related("roles"):
                synchronise_staff_groups(instructor)
        _audit(request, ActivityLog.Action.UPDATE, instance, "Staff directory entry status updated.")
    else:
        raise ValidationError("Choose a valid staff or team action.")


@login_required
@require_http_methods(["GET", "POST"])
def staff_access(request):
    if not can_view_staff(request.user):
        raise PermissionDenied("Your account does not have access to Staff.")
    editable = can_edit_staff(request.user)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied("Staff changes require administrator edit access.")
        try:
            with transaction.atomic():
                _mutate(request)
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
        except IntegrityError:
            messages.error(request, "This name or account already exists. Check the details and try again.")
        else:
            messages.success(request, "Staff and teams updated.")
        query = request.GET.urlencode()
        return redirect(reverse("staff") + ("?" + query if query else ""))

    instructors = Instructor.objects.select_related("team", "user").prefetch_related("roles", "user__groups", "user__tms_permission_overrides")
    q = request.GET.get("q", "").strip()[:200]
    if q:
        instructors = instructors.filter(Q(name_english__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q) | Q(team__name__icontains=q) | Q(roles__name__icontains=q)).distinct()
    for key, lookup in (("name", "name_english__icontains"), ("phone", "phone__icontains"), ("email", "email__icontains")):
        value = request.GET.get(key, "").strip()[:255]
        if value:
            instructors = instructors.filter(**{lookup: value})
    team_id = request.GET.get("team", "")[:19]
    if team_id.isdecimal() and 0 < int(team_id) <= 9223372036854775807:
        instructors = instructors.filter(team_id=int(team_id))
    role_id = request.GET.get("position", "")[:19]
    if role_id.isdecimal() and 0 < int(role_id) <= 9223372036854775807:
        instructors = instructors.filter(roles__pk=int(role_id))
    rows = []
    for instructor in instructors:
        defaults = role_feature_access(instructor.user, instructor=instructor) if instructor.user_id else {}
        effective = feature_permissions(instructor.user, instructor=instructor) if instructor.user_id else {}
        overrides = {item.feature: item.level for item in instructor.user.tms_permission_overrides.all()} if instructor.user_id else {}
        permissions = [{"feature": feature, "label": label, "default": defaults.get(feature, ""), "level": effective.get(feature, ""), "override": overrides.get(feature, "")} for feature, label in STAFF_FEATURES]
        rows.append({"staff": instructor, "permissions": permissions, "editable": editable and (not instructor.user_id or not instructor.user.is_superuser or request.user.is_superuser)})
    return render(request, "portal/staff_access.html", {
        "rows": rows, "teams": Team.objects.select_related("leader").all(),
        "roles": InstructorRole.objects.all(), "staff_editable": editable,
        "all_instructors": Instructor.objects.filter(active=True).order_by("name_english"),
        "staff_features": STAFF_FEATURES, "q": q, "selected_team": team_id,
        "selected_position": role_id,
        "current": request.GET,
        "editing": editable and request.GET.get("edit") == "yes",
    })
