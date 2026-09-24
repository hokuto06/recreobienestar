"""
Phase 4B-4: the one place a Mercado Pago payment is ever turned into a
COMPLETED/FAILED/PENDING/REFUNDED OfferingPurchase. Extracted out of the
webhook (4B-2), which had this logic inline, so the return-URL
verification path (4B-4, payments/views.py's /pago/exito|pendiente/
views) reuses the exact same decision logic instead of a second,
potentially-drifting copy.

Both payments.views.MercadoPagoWebhookView (POST /api/mercadopago/
webhook/) and the /pago/* return views call apply_payment_to_purchase()
with an already re-queried, authoritative Mercado Pago payment object
(see payments.views._fetch_mp_payment) — this function itself never
calls out to Mercado Pago and never sees a raw request; it only ever
acts on a `payment` dict the caller already fetched straight from MP's
API. Neither caller ever passes this something derived from an
unverified webhook body or an unverified return-URL query string.
"""
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db import transaction

from .models import OfferingPurchase, PurchaseStatus

logger = logging.getLogger(__name__)


def _amount_mismatches(purchase, payment):
    """True if what Mercado Pago says was actually paid doesn't match the
    price snapshot taken at checkout time (payments.views.
    CheckoutInitiationView). Defense in depth against a tampered/replayed
    notification trying to unlock a purchase for less than it costs.

    Any comparison failure (missing/malformed transaction_amount, no price
    snapshot to compare against — the latter can't happen for a 4B-1+
    purchase, only theoretically for a pre-4B-1 hand-created one) is
    treated as a mismatch: fail closed, never fail open."""
    if purchase.amount is None:
        return True
    try:
        reported_amount = Decimal(str(payment.get('transaction_amount'))).quantize(Decimal('0.01'))
    except (TypeError, InvalidOperation):
        return True
    reported_currency = (payment.get('currency_id') or '').upper()
    expected_currency = (purchase.currency or '').upper()
    return reported_amount != purchase.amount or reported_currency != expected_currency


def _map_purchase_status(mp_status):
    """Mercado Pago's payment.status vocabulary -> this app's
    PurchaseStatus. Returns None for a recognized-but-not-actionable MP
    status (e.g. authorized, in_mediation, charged_back) — the caller
    still records mp_status/mp_payment_id but leaves `status` untouched
    rather than guessing at a mapping the product hasn't decided on."""
    if mp_status == 'approved':
        return PurchaseStatus.COMPLETED
    if mp_status in ('rejected', 'cancelled'):
        return PurchaseStatus.FAILED
    if mp_status in ('pending', 'in_process'):
        return PurchaseStatus.PENDING
    if mp_status == 'refunded':
        return PurchaseStatus.REFUNDED
    return None


@dataclass
class PaymentApplicationResult:
    """What happened when an already-fetched MP payment was applied to a
    purchase. Callers (the webhook's response code, the return views'
    template choice) branch on `reason` — never re-derive it themselves.

    reason values:
      'invalid_external_reference' — payment['external_reference'] isn't
        a usable purchase id; `purchase` is None.
      'unknown_purchase' — external_reference parsed fine but no
        OfferingPurchase with that id exists; `purchase` is None.
      'wrong_user' — `expected_user` was given and didn't match the
        purchase's owner; `purchase` is deliberately None (never handed
        back to a caller that isn't allowed to see it), nothing touched.
      'already_completed' — the purchase was already COMPLETED; left
        untouched, no-op (the idempotency guard).
      'amount_mismatch' — MP's reported amount/currency didn't match the
        checkout-time snapshot; purchase left/set PENDING for review.
      'ok' — applied normally; purchase.status reflects the mapped
        outcome (COMPLETED/FAILED/PENDING/REFUNDED), or is unchanged if
        mp_status wasn't a mapped/actionable value.
    """
    purchase: Optional[OfferingPurchase]
    mp_payment_id: str
    mp_status: str
    reason: str


def apply_payment_to_purchase(payment, fallback_payment_id=None, expected_user=None):
    """Maps `payment` (an authoritative Mercado Pago payment object,
    already re-queried by the caller) to an OfferingPurchase via
    payment['external_reference'] and applies it: amount/currency
    validation, status mapping, and an idempotent, concurrency-safe
    (select_for_update) completion.

    This is the ONLY function in the codebase that ever sets
    OfferingPurchase.status to COMPLETED off a Mercado Pago payment.

    `fallback_payment_id`: used only if `payment` itself carries no
    usable 'id' — shouldn't happen with a real MP response, but mirrors
    the original webhook's defensive `payment.get('id') or data_id`.

    `expected_user`: only the /pago/* return views pass this (the
    webhook never does — it has no concept of "the current visitor").
    When given, the resolved purchase must belong to this user or
    NOTHING is applied (reason='wrong_user', `purchase` comes back as
    None). This is what stops a logged-in visitor from completing
    someone else's purchase by pasting a different payment_id/
    external_reference into the return URL — checked before any other
    branch below, including the amount/status logic, so an
    unauthorized caller never even learns whether the purchase is
    already completed.

    IDEMPOTENCY / CONCURRENCY (unchanged from the original webhook-only
    version): the purchase row is locked with select_for_update() inside
    an atomic transaction, and an already-COMPLETED purchase is always a
    no-op regardless of what `payment` says — so calling this twice for
    the same purchase (e.g. the webhook fires AND the buyer's return
    visit both resolve it), or concurrently, can never re-process or
    downgrade a settled purchase.
    """
    mp_payment_id = str(payment.get('id') or fallback_payment_id or '')
    mp_status = payment.get('status', '')
    external_reference = payment.get('external_reference')

    try:
        purchase_id = int(external_reference)
    except (TypeError, ValueError):
        logger.warning(
            'Mercado Pago payment %s has missing/invalid external_reference=%r — '
            'ignoring, no purchase created or changed',
            mp_payment_id, external_reference,
        )
        return PaymentApplicationResult(
            purchase=None, mp_payment_id=mp_payment_id, mp_status=mp_status,
            reason='invalid_external_reference',
        )

    with transaction.atomic():
        purchase = OfferingPurchase.objects.select_for_update().filter(pk=purchase_id).first()
        if purchase is None:
            logger.warning(
                'Mercado Pago payment %s references unknown OfferingPurchase %s',
                mp_payment_id, purchase_id,
            )
            return PaymentApplicationResult(
                purchase=None, mp_payment_id=mp_payment_id, mp_status=mp_status,
                reason='unknown_purchase',
            )

        if expected_user is not None and purchase.user_id != expected_user.id:
            logger.warning(
                'Mercado Pago payment %s: purchase %s belongs to a different user — refusing '
                'to apply (expected_user=%s)',
                mp_payment_id, purchase.id, expected_user.id,
            )
            return PaymentApplicationResult(
                purchase=None, mp_payment_id=mp_payment_id, mp_status=mp_status,
                reason='wrong_user',
            )

        if purchase.status == PurchaseStatus.COMPLETED:
            # Idempotent no-op — a retried/duplicate/stale notification,
            # or the buyer's return-page visit arriving after the webhook
            # (or vice versa) already settled it. Never re-process, never
            # downgrade.
            logger.info(
                'Mercado Pago payment %s: purchase %s already COMPLETED — ignoring (mp_status=%s)',
                mp_payment_id, purchase.id, mp_status,
            )
            return PaymentApplicationResult(
                purchase=purchase, mp_payment_id=mp_payment_id, mp_status=mp_status,
                reason='already_completed',
            )

        purchase.mp_payment_id = mp_payment_id
        purchase.mp_status = mp_status

        if _amount_mismatches(purchase, payment):
            # Defense in depth: a payment MP confirms as real that
            # nonetheless doesn't match what was quoted at checkout.
            # Never mark COMPLETED off this; leave PENDING (mp_status is
            # saved, so Carla can see what MP actually reported) so it
            # surfaces for manual review in the admin.
            logger.error(
                'Mercado Pago payment %s: AMOUNT/CURRENCY MISMATCH on purchase %s — expected '
                '%s %s, MP reported %s %s (mp_status=%s) — left PENDING for manual review',
                mp_payment_id, purchase.id, purchase.amount, purchase.currency,
                payment.get('transaction_amount'), payment.get('currency_id'), mp_status,
            )
            purchase.status = PurchaseStatus.PENDING
            purchase.save(update_fields=['mp_payment_id', 'mp_status', 'status', 'updated_at'])
            return PaymentApplicationResult(
                purchase=purchase, mp_payment_id=mp_payment_id, mp_status=mp_status,
                reason='amount_mismatch',
            )

        new_status = _map_purchase_status(mp_status)
        if new_status is not None:
            purchase.status = new_status
        purchase.save(update_fields=['mp_payment_id', 'mp_status', 'status', 'updated_at'])

    logger.info(
        'Mercado Pago payment %s: purchase %s -> status=%s (mp_status=%s)',
        mp_payment_id, purchase.id, purchase.status, mp_status,
    )
    return PaymentApplicationResult(
        purchase=purchase, mp_payment_id=mp_payment_id, mp_status=mp_status, reason='ok',
    )
