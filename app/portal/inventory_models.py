"""Auditable stock issues; balances stay in the established inventory models."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class WarehouseStockPolicy(models.Model):
    item = models.OneToOneField("portal.InventoryItem", on_delete=models.CASCADE, related_name="stock_policy")
    minimum_quantity = models.PositiveIntegerField(default=0)
    maximum_quantity = models.PositiveIntegerField(default=0)
    lead_time_days = models.PositiveIntegerField(default=0)


class InventoryIssue(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    instructor = models.ForeignKey("portal.Instructor", on_delete=models.PROTECT, related_name="stock_issues")
    notes = models.CharField(max_length=1000, blank=True)
    issued_on = models.DateField(default=timezone.localdate)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_stock_issues")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="updated_stock_issues")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_stock_issues")

    class Meta:
        ordering = ["-created_at", "-pk"]


class InventoryIssueLine(models.Model):
    issue = models.ForeignKey(InventoryIssue, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey("portal.InventoryItem", on_delete=models.PROTECT, related_name="issue_lines")
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ["item__name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["issue", "item"], name="unique_stock_issue_item"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="stock_issue_positive_quantity"),
        ]
