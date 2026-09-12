"""
Payments domain — Phase 4A: one-time Offering purchases, recorded
provider-agnostically (no Mercado Pago integration yet — that's Phase 4B).

OfferingPurchase exists so a purchase can be recorded (today, by hand from
the admin — see PHASE4A note on OfferingPurchaseAdmin) and, once
COMPLETED, grants access to the offering's videos alongside membership
access. See memberships.services.can_access_video and
user_has_purchased_offering_unlocking, which OR this in as an ADDITIONAL
access path — never a replacement for the membership check.
"""
from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class PurchaseStatus(models.TextChoices):
    PENDING = 'pending', 'Pendiente'
    COMPLETED = 'completed', 'Completada'
    FAILED = 'failed', 'Fallida'
    REFUNDED = 'refunded', 'Reembolsada'


class OfferingPurchaseQuerySet(models.QuerySet):
    def completed(self):
        return self.filter(status=PurchaseStatus.COMPLETED)

    def unlocking(self, user, video):
        """COMPLETED purchases by `user` of an Offering that includes
        `video` among its videos — the query-level equivalent of "does
        this purchase grant access to this video". Returns an empty
        queryset (never raises) for an anonymous/None user."""
        if user is None or not getattr(user, 'is_authenticated', False):
            return self.none()
        return self.completed().filter(user=user, offering__videos=video)


class OfferingPurchase(TimeStampedModel):
    """A user's purchase of a one-time Offering (site_content.Offering).

    Only a COMPLETED purchase grants access — PENDING/FAILED/REFUNDED
    purchases are recorded (so Carla has a paper trail) but grant nothing.
    `status` is set by hand from the admin in this phase, so 4A can be
    tested end-to-end without a live payment provider; Phase 4B will add
    the fields that let a webhook flip it automatically (Mercado Pago
    payment id/status), but none of that exists yet — deliberately kept
    provider-agnostic for now.
    """
    objects = OfferingPurchaseQuerySet.as_manager()

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='offering_purchases',
    )
    offering = models.ForeignKey(
        'site_content.Offering', on_delete=models.PROTECT, related_name='purchases',
    )
    status = models.CharField(
        max_length=20, choices=PurchaseStatus.choices, default=PurchaseStatus.PENDING,
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Compra de propuesta'
        verbose_name_plural = 'Compras de propuestas'

    def __str__(self):
        return f'{self.user} — {self.offering} ({self.get_status_display()})'
