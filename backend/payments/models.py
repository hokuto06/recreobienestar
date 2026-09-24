"""
Payments domain. Phase 4A built OfferingPurchase provider-agnostically (no
Mercado Pago integration). Phase 4B-1 adds the MP-specific fields
(mp_preference_id/mp_payment_id/mp_status) and a price snapshot
(amount/currency) — all nullable/blank, so the 4A manual-purchase-in-admin
flow keeps working exactly as before with these left empty.

OfferingPurchase exists so a purchase can be recorded — either by hand
from the admin (4A) or, from 4B-1 on, via a real Mercado Pago Checkout Pro
preference (see payments/views.py:CheckoutInitiationView) — and, once
COMPLETED, grants access to the offering's videos alongside membership
access. See memberships.services.can_access_video and
user_has_purchased_offering_unlocking, which OR this in as an ADDITIONAL
access path — never a replacement for the membership check.

Phase 4B-1 explicitly does NOT flip a purchase to COMPLETED on its own —
that only happens once 4B-2's webhook exists (or by hand, as today). This
sub-phase only gets as far as PENDING + a preference id.
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
    In 4A, `status` was set by hand from the admin. From 4B-1 on, a
    checkout-initiation request (payments.views.CheckoutInitiationView)
    creates one of these as PENDING and attaches `mp_preference_id`
    immediately — `status` still only ever becomes COMPLETED by hand
    (4A) or, once 4B-2 exists, by the Mercado Pago webhook; nothing in
    4B-1 completes a purchase on its own.
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

    # ── Phase 4B-1: price snapshot ───────────────────────────────────────
    # offering.price/currency can change after the fact; these two capture
    # what the buyer was actually quoted at checkout time, so a later price
    # edit never silently reinterprets a past purchase. Nullable: 4A's
    # manual admin-created purchases predate this and never set them.
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default='')

    # ── Phase 4B-1/4B-2: Mercado Pago identifiers ────────────────────────
    # mp_preference_id is set by CheckoutInitiationView right after MP
    # confirms the preference (4B-1). mp_payment_id/mp_status are left
    # empty here on purpose — populated by 4B-2's webhook once a payment
    # actually happens; this sub-phase never touches them.
    mp_preference_id = models.CharField(max_length=100, blank=True, default='')
    mp_payment_id = models.CharField(max_length=100, blank=True, null=True)
    # MP's own raw status vocabulary (approved/pending/rejected/
    # in_process/refunded/charged_back/...) — kept separate from our own
    # `status` (PurchaseStatus) rather than reusing it, since MP's has
    # more/different values and this app's access-granting logic must
    # only ever read `status`, never this field.
    mp_status = models.CharField(max_length=30, blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Compra de propuesta'
        verbose_name_plural = 'Compras de propuestas'
        indexes = [models.Index(fields=['mp_preference_id'])]

    def __str__(self):
        return f'{self.user} — {self.offering} ({self.get_status_display()})'
