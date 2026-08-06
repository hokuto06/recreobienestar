from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OrderedActiveModel(models.Model):
    """Shared by Category/Program/MembershipPlan: a manually curated display
    order plus an active/inactive toggle that hides a row without deleting
    it."""
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(
        default=0,
        help_text='Menor valor aparece primero.',
    )

    class Meta:
        abstract = True
        ordering = ['display_order', 'id']
