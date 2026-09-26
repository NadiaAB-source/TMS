"""Atomic issue, correction and reversal of warehouse/instructor balances."""
import uuid
from datetime import date
from collections import defaultdict
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone
from .access import can_manage_inventory
from .models import ActivityLog, Instructor, InstructorInventoryBalance, InstructorInventoryMovement, InventoryItem
from .inventory_models import InventoryIssue, InventoryIssueLine


def _lines(values):
    result = {}
    for item_id, quantity in values:
        try:
            item_id, quantity = int(item_id), int(quantity)
        except (TypeError, ValueError):
            raise ValidationError("Choose an item and enter a whole-number quantity for every line.")
        if item_id in result:
            raise ValidationError("Choose each stock item only once in a transaction.")
        if not 1 <= quantity <= 2147483647:
            raise ValidationError("Each issued quantity must be a positive whole number within the supported stock range.")
        result[item_id] = quantity
    if not result:
        raise ValidationError("Add at least one item to the transaction.")
    if len(result) > 100:
        raise ValidationError("A transaction can contain at most 100 items.")
    return result


@transaction.atomic
def save_issue(*, actor, instructor_id=None, lines=(), notes="", issue_id=None,
               token=None, expected_revision=None, reverse=False, ip_address=None, issued_on=None):
    if not can_manage_inventory(actor):
        raise PermissionDenied("Inventory edit permission is required.")
    # Serialize one actor's form retries before checking their submission token.
    # PostgreSQL's weaker row lock permits audit-log FK inserts from concurrent
    # course reports, avoiding a user-row/balance-row lock inversion.
    actor_rows = get_user_model().objects.only("pk")
    actor_rows = actor_rows.select_for_update(no_key=True) if connection.vendor == "postgresql" else actor_rows.select_for_update()
    actor_rows.get(pk=actor.pk)
    issue = None
    old = {}
    if issue_id:
        issue = InventoryIssue.objects.select_for_update().get(public_id=issue_id)
        if issue.reversed_at:
            raise ValidationError("This transaction has already been reversed.")
        try:
            expected_revision = int(expected_revision)
        except (TypeError, ValueError):
            raise ValidationError("Reload the transaction before saving; its revision is missing or invalid.")
        if expected_revision != issue.revision:
            raise ValidationError("This transaction changed. Reload it before saving.")
        old = dict(issue.lines.values_list("item_id", "quantity"))
    elif reverse:
        raise ValidationError("Choose the transaction to reverse.")
    if not issue and token:
        try:
            token = uuid.UUID(str(token))
        except (TypeError, ValueError):
            raise ValidationError("Reload the page to obtain a valid transaction submission identifier.")
        # A browser retry of the same successful submission must not issue twice.
        previous = InventoryIssue.objects.filter(public_id=token).first()
        if previous:
            if previous.created_by_id != actor.pk:
                raise PermissionDenied("This submission identifier is already in use.")
            return previous
    new = {} if reverse else _lines(lines)
    if not reverse:
        try:
            issued_on = date.fromisoformat(issued_on) if isinstance(issued_on, str) else issued_on or timezone.localdate()
        except ValueError:
            raise ValidationError("Enter a valid transaction date.")
    try:
        instructor = issue.instructor if reverse else Instructor.objects.get(pk=int(instructor_id), active=True)
    except (Instructor.DoesNotExist, TypeError, ValueError):
        raise ValidationError("Choose an active instructor to receive the stock.")
    item_ids = sorted(set(old) | set(new))
    items = {item.pk: item for item in InventoryItem.objects.select_for_update().filter(pk__in=item_ids).order_by("pk")}
    if len(items) != len(item_ids) or any(not items[key].active for key in new):
        raise ValidationError("One or more stock items are no longer available.")
    changes = defaultdict(int)
    for item_id, quantity in old.items():
        changes[(issue.instructor_id, item_id)] -= quantity
    for item_id, quantity in new.items():
        changes[(instructor.pk, item_id)] += quantity
    balances = {}
    for key in sorted(changes):
        balance, _ = InstructorInventoryBalance.objects.select_for_update().get_or_create(
            instructor_id=key[0], item_id=key[1], defaults={"updated_by": actor})
        if balance.quantity_on_hand + changes[key] < 0:
            raise ValidationError(f"{items[key[1]].name}: stock already used by this instructor cannot be reversed or removed.")
        if balance.quantity_on_hand + changes[key] > 2147483647:
            raise ValidationError("The resulting instructor balance exceeds the supported stock range.")
        balances[key] = balance
    for item_id in item_ids:
        warehouse_change = old.get(item_id, 0) - new.get(item_id, 0)
        if items[item_id].quantity_on_hand + warehouse_change < 0:
            raise ValidationError(f"{items[item_id].name}: only {items[item_id].quantity_on_hand} {items[item_id].unit} remain in warehouse stock.")
        if items[item_id].quantity_on_hand + warehouse_change > 2147483647:
            raise ValidationError("The resulting warehouse balance exceeds the supported stock range.")
    previous = {"instructor_id": issue.instructor_id, "items": old, "notes": issue.notes, "issued_on": issue.issued_on.isoformat()} if issue else None
    if not issue:
        issue = InventoryIssue.objects.create(public_id=token or uuid.uuid4(), instructor=instructor,
            notes=notes.strip()[:1000], issued_on=issued_on, created_by=actor, updated_by=actor)
    else:
        issue.revision += 1
        issue.updated_by = actor
        if reverse:
            issue.reversed_at, issue.reversed_by = timezone.now(), actor
        else:
            issue.instructor, issue.notes = instructor, notes.strip()[:1000]
            issue.issued_on = issued_on
        issue.save()
    for item_id, item in items.items():
        delta = old.get(item_id, 0) - new.get(item_id, 0)
        if delta:
            item.quantity_on_hand += delta
            item.save(update_fields=["quantity_on_hand", "updated_at"])
    for key, delta in changes.items():
        if not delta:
            continue
        balance = balances[key]
        balance.quantity_on_hand += delta
        balance.updated_by = actor
        balance.save(update_fields=["quantity_on_hand", "updated_by", "updated_at"])
        InstructorInventoryMovement.objects.create(instructor_id=key[0], item_id=key[1],
            movement_type="issue" if previous is None else "correction", quantity_change=delta,
            balance_after=balance.quantity_on_hand, recorded_by=actor,
            notes=f"Stock transaction {issue.public_id}; revision {issue.revision}" + ("; reversed" if reverse else ""))
    if not reverse:
        issue.lines.exclude(item_id__in=new).delete()
        for item_id, quantity in new.items():
            InventoryIssueLine.objects.update_or_create(issue=issue, item_id=item_id, defaults={"quantity": quantity})
    ActivityLog.objects.create(actor=actor, action=ActivityLog.Action.DELETE if reverse else ActivityLog.Action.UPDATE if previous else ActivityLog.Action.ISSUE,
        object_type="InventoryIssue", object_id=str(issue.public_id),
        description="Stock issue reversed; history retained." if reverse else "Stock issue saved.",
        details={"before": previous, "after": {"instructor_id": instructor.pk, "items": new,
            "notes": issue.notes, "revision": issue.revision, "issued_on": issue.issued_on.isoformat()}, "reversed": reverse}, ip_address=ip_address)
    return issue
