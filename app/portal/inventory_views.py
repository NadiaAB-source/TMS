import uuid
from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .access import can_manage_inventory, can_view_inventory, instructor_for_user
from .inventory_models import InventoryIssue, WarehouseStockPolicy
from .inventory_services import save_issue
from .models import ActivityLog, Instructor, InstructorInventoryBalance, InstructorInventoryMovement, InventoryItem, Team
from .security import get_client_ip


def _quantity(data, field):
    try:
        value = int(data.get(field, 0) or 0)
    except (TypeError, ValueError):
        raise ValidationError("Enter whole-number stock quantities and lead times.")
    if not 0 <= value <= 2147483647:
        raise ValidationError("Enter stock quantities and lead times between 0 and 2147483647.")
    return value


@transaction.atomic
def _save_item(request):
    item_id = request.POST.get("item_id")
    item = get_object_or_404(InventoryItem.objects.select_for_update(), pk=item_id) if item_id else InventoryItem()
    before = {"name": item.name, "unit": item.unit, "warehouse_quantity": item.quantity_on_hand} if item.pk else None
    name = request.POST.get("name", "").strip()
    unit = request.POST.get("unit", "").strip()
    category = request.POST.get("category", "equipment")
    if not name or len(name) > 200 or not unit or len(unit) > 50:
        raise ValidationError("Enter an item name (up to 200 characters) and unit (up to 50 characters).")
    if category not in InventoryItem.Category.values:
        raise ValidationError("Choose a valid stock category.")
    if InventoryItem.objects.filter(name__iexact=name).exclude(pk=item.pk).exists():
        raise ValidationError("An inventory item with this name already exists.")
    minimum = _quantity(request.POST, "minimum_quantity")
    maximum = _quantity(request.POST, "maximum_quantity")
    if maximum < minimum:
        raise ValidationError("The stock maximum must be at least the minimum.")
    item.name, item.unit, item.category = name, unit, category
    item.quantity_on_hand = _quantity(request.POST, "quantity_on_hand")
    item.full_clean()
    item.save()
    WarehouseStockPolicy.objects.update_or_create(item=item, defaults={"minimum_quantity": minimum,
        "maximum_quantity": maximum, "lead_time_days": _quantity(request.POST, "lead_time_days")})
    ActivityLog.objects.create(actor=request.user, action="update" if before else "create", object_type="InventoryItem",
        object_id=str(item.pk), description="Warehouse item and stock policy saved.", ip_address=get_client_ip(request),
        details={"before": before, "after": {"name": name, "unit": unit, "warehouse_quantity": item.quantity_on_hand,
            "minimum": minimum, "maximum": maximum, "lead_time_days": _quantity(request.POST, "lead_time_days")}})


@login_required
@require_http_methods(["GET", "POST"])
def inventory(request):
    if not can_view_inventory(request.user):
        raise PermissionDenied("Inventory access is not enabled for this account.")
    manager = can_manage_inventory(request.user)
    if request.method == "POST":
        if not manager:
            raise PermissionDenied("Inventory edit permission is required.")
        action = request.POST.get("action", "")
        # Existing bookmarked forms remain valid; the new page exposes only the
        # warehouse issue workflow requested in the design.
        if action in {"allocate", "add", "count"}:
            from .course_workflow_views import inventory as legacy_inventory
            return legacy_inventory(request)
        try:
            if action == "save_item":
                _save_item(request)
            elif action in {"save_issue", "reverse_issue"}:
                item_ids, quantities = request.POST.getlist("line_item"), request.POST.getlist("line_quantity")
                if len(item_ids) != len(quantities):
                    raise ValidationError("Each transaction item requires a quantity.")
                pairs = [(item, quantity) for item, quantity in zip(item_ids, quantities) if item or quantity]
                save_issue(actor=request.user, instructor_id=request.POST.get("instructor_id"), lines=pairs,
                    notes=request.POST.get("notes", ""), issue_id=request.POST.get("issue_id") or None,
                    token=request.POST.get("token") or None, expected_revision=request.POST.get("revision"),
                    issued_on=request.POST.get("issued_on") or timezone.localdate(),
                    reverse=action == "reverse_issue", ip_address=get_client_ip(request))
            else:
                raise ValidationError("Choose a valid inventory action.")
        except (ValidationError, ValueError, Instructor.DoesNotExist, InventoryIssue.DoesNotExist, IntegrityError) as exc:
            for error in exc.messages if isinstance(exc, ValidationError) else ["The transaction could not be saved. Reload the page and check its fields."]:
                messages.error(request, error)
        else:
            messages.success(request, "Transaction reversed; its history is retained." if action == "reverse_issue" else "Inventory updated.")
        return redirect("inventory")

    items = list(InventoryItem.objects.select_related("stock_policy").all())
    for item in items:
        policy = getattr(item, "stock_policy", None)
        target = policy.maximum_quantity if policy else 0
        item.stock_percentage = round(100 * item.quantity_on_hand / target) if target else None
        item.stock_tone = ("good" if item.quantity_on_hand >= target else "warn" if item.quantity_on_hand * 2 >= target else "low") if target else "neutral"
        days = policy.lead_time_days if policy else 0
        item.lead_time_display = f"{days // 7} weeks" if days and days % 7 == 0 else f"{days} days"
    editing_issue = None
    if request.GET.get("edit_issue") and manager:
        try:
            issue_id = uuid.UUID(request.GET["edit_issue"])
        except (ValueError, TypeError):
            messages.error(request, "Choose a valid transaction to edit.")
            return redirect("inventory")
        editing_issue = get_object_or_404(InventoryIssue.objects.prefetch_related("lines__item"), public_id=issue_id, reversed_at__isnull=True)
    editing_item = None
    if request.GET.get("edit_item") and manager:
        if not request.GET["edit_item"].isdecimal():
            messages.error(request, "Choose a valid stock item to edit.")
            return redirect("inventory")
        editing_item = get_object_or_404(InventoryItem.objects.select_related("stock_policy"), pk=request.GET["edit_item"])
    issues = InventoryIssue.objects.select_related("instructor", "created_by", "reversed_by").prefetch_related("lines__item").order_by("-issued_on", "-created_at", "-pk")
    balances = InstructorInventoryBalance.objects.select_related("instructor", "instructor__team", "item").filter(quantity_on_hand__gt=0)
    movements = InstructorInventoryMovement.objects.select_related("instructor", "item", "session", "session__course")
    # A granted Inventory View permission covers the warehouse and its records;
    # ordinary instructors have no inventory-tab permission by default.
    q, team = request.GET.get("q", "").strip(), request.GET.get("team", "").strip()
    if q:
        matching = Q(instructor__name_english__icontains=q) | Q(item__name__icontains=q)
        balances, movements = balances.filter(matching), movements.filter(matching)
        issues = issues.filter(Q(instructor__name_english__icontains=q) | Q(lines__item__name__icontains=q)).distinct()
    if team.isdecimal():
        balances, movements = balances.filter(instructor__team_id=team), movements.filter(instructor__team_id=team)
    movement_type = request.GET.get("movement_type", "")
    if movement_type in InstructorInventoryMovement.MovementType.values:
        movements = movements.filter(movement_type=movement_type)
    date_from, date_to = request.GET.get("date_from", ""), request.GET.get("date_to", "")
    try:
        if date_from:
            movements = movements.filter(created_at__date__gte=date.fromisoformat(date_from))
        if date_to:
            movements = movements.filter(created_at__date__lte=date.fromisoformat(date_to))
    except ValueError:
        messages.error(request, "Enter valid dates for the inventory history filter.")
        movements = movements.none()
    view = "history" if request.GET.get("view") == "history" else "balances"
    return render(request, "portal/inventory.html", {"items": items, "inventory_manager": manager,
        "instructors": Instructor.objects.filter(active=True).order_by("name_english"), "teams": Team.objects.filter(active=True),
        "transaction_page": Paginator(issues, 20).get_page(request.GET.get("transactions_page")),
        "editing_issue": editing_issue, "editing_item": editing_item, "transaction_token": str(uuid.uuid4()), "today": timezone.localdate(),
        "inventory_view": view, "balance_page": Paginator(balances, 25).get_page(request.GET.get("page")),
        "movement_page": Paginator(movements, 25).get_page(request.GET.get("page")),
        "balance_count": balances.count(), "history_count": movements.count(),
        "filter_q": q, "filter_team": team, "filter_movement": movement_type,
        "filter_date_from": date_from, "filter_date_to": date_to,
        "movement_types": InstructorInventoryMovement.MovementType.choices})
