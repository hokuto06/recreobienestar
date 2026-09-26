"""
Phase 5B-2b: Mercado Pago SUBSCRIPTION notifications — the two types the
offering webhook never handled:

  type=subscription_authorized_payment  data.id = authorized-payment id
      One recurring charge attempt. Re-fetched with
      sdk.invoice().get(id) (GET /authorized_payments/{id}), which carries
      preapproval_id, transaction_amount/currency_id, external_reference
      and the nested payment {id, status}.
  type=subscription_preapproval         data.id = preapproval id
      The subscription itself changed state (authorized/paused/
      cancelled). Re-fetched with sdk.preapproval().get(id).

Called ONLY from payments.views.MercadoPagoWebhookView, AFTER its
signature verification has passed, for any notification whose `type`
isn't "payment" (type=payment — including subscription charges carrying a
"sub-<id>" reference — still goes down the untouched offering path, which
ignores those references). Same trust model as that view: the
notification body is never trusted; only the object re-fetched from MP's
API is acted on.

Response contract (handle_subscription_notification returns the status
code for the view to send): 200 for everything handled or deliberately
ignored — unknown type, no matching Subscription, amount mismatch,
duplicate — since none of those benefit from MP retrying; 502 only when
the re-fetch from MP itself failed, where a retry IS wanted.

NEVER creates a Subscription: a notification that doesn't map to an
existing row (via mp_preapproval_id) is logged and dropped.
"""
import logging
from decimal import Decimal, InvalidOperation

import mercadopago
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from common.choices import SubscriptionStatus

from .models import Subscription, SubscriptionCharge, SubscriptionChargeOutcome
from .services import paid_period_end, supersede_active_trial

logger = logging.getLogger(__name__)

AUTHORIZED_PAYMENT = 'subscription_authorized_payment'
PREAPPROVAL = 'subscription_preapproval'

_APPROVED = 'approved'
_FAILED = ('rejected', 'cancelled')


def handle_subscription_notification(notification_type, data_id):
    """Entry point from the webhook view. Returns an HTTP status code."""
    if notification_type == AUTHORIZED_PAYMENT:
        handler = process_authorized_payment
    elif notification_type == PREAPPROVAL:
        handler = process_preapproval
    else:
        logger.info(
            'Mercado Pago webhook: ignoring unhandled notification type=%r data.id=%s',
            notification_type, data_id,
        )
        return 200
    if not data_id:
        logger.info('Mercado Pago webhook: type=%s without data.id — nothing to do', notification_type)
        return 200
    return handler(data_id)


def _sdk():
    return mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)


def _reference_matches(subscription, external_reference):
    """Cross-check of MP's external_reference against the Subscription
    found via mp_preapproval_id (the primary anchor). Accepts the current
    "sub-<id>" format and the bare "<id>" format the first production
    preapproval (subscription 5) was created with before the prefix fix.
    An absent reference is not a contradiction."""
    if not external_reference:
        return True
    reference = str(external_reference)
    return reference in (f'sub-{subscription.id}', str(subscription.id))


def _find_subscription(preapproval_id, external_reference, context):
    """Locked Subscription for `preapproval_id`, or None (logged). Must be
    called inside transaction.atomic()."""
    subscription = (
        Subscription.objects.select_for_update().select_related('plan', 'user')
        .filter(mp_preapproval_id=preapproval_id).first()
    )
    if subscription is None:
        logger.warning(
            'Mercado Pago %s: no Subscription with mp_preapproval_id=%s (external_reference=%r) — '
            'ignoring, nothing created or changed',
            context, preapproval_id, external_reference,
        )
        return None
    if not _reference_matches(subscription, external_reference):
        logger.error(
            'Mercado Pago %s: preapproval %s maps to Subscription %s but external_reference=%r '
            'disagrees — ignoring, nothing changed',
            context, preapproval_id, subscription.id, external_reference,
        )
        return None
    return subscription


def _amount_mismatches(subscription, authorized_payment):
    """Same fail-closed rule as payments.services._amount_mismatches, but
    against the Subscription's signup-time snapshot."""
    if subscription.amount is None:
        return True
    try:
        reported = Decimal(str(authorized_payment.get('transaction_amount'))).quantize(Decimal('0.01'))
    except (TypeError, InvalidOperation):
        return True
    reported_currency = (authorized_payment.get('currency_id') or '').upper()
    return reported != subscription.amount or reported_currency != (subscription.currency or '').upper()


# ── subscription_authorized_payment ────────────────────────────────────


def process_authorized_payment(authorized_payment_id):
    """One recurring charge attempt. See module docstring for the trust
    model; see SubscriptionCharge for the idempotency ledger."""
    try:
        result = _sdk().invoice().get(authorized_payment_id)
        result.raise_for_status()
        authorized_payment = result['response']
    except Exception:
        logger.exception(
            'Mercado Pago webhook: authorized payment lookup failed for data.id=%s', authorized_payment_id,
        )
        return 502

    preapproval_id = authorized_payment.get('preapproval_id')
    payment = authorized_payment.get('payment') or {}
    mp_payment_id = str(payment.get('id') or '')
    mp_payment_status = payment.get('status') or ''
    if not preapproval_id:
        logger.warning('Mercado Pago authorized payment %s has no preapproval_id — ignoring', authorized_payment_id)
        return 200

    with transaction.atomic():
        subscription = _find_subscription(
            preapproval_id, authorized_payment.get('external_reference'),
            f'authorized payment {authorized_payment_id}',
        )
        if subscription is None:
            return 200
        if not mp_payment_id:
            # Scheduled/not yet attempted — no payment to act on yet.
            logger.info(
                'Mercado Pago authorized payment %s (Subscription %s): no payment attempted yet '
                '(status=%s) — nothing to do',
                authorized_payment_id, subscription.id, authorized_payment.get('status'),
            )
            return 200

        charge = SubscriptionCharge.objects.select_for_update().filter(mp_payment_id=mp_payment_id).first()
        if charge is not None:
            if charge.subscription_id != subscription.id:
                logger.error(
                    'Mercado Pago payment %s already recorded for Subscription %s, now reported for '
                    'Subscription %s — ignoring',
                    mp_payment_id, charge.subscription_id, subscription.id,
                )
                return 200
            if charge.mp_payment_status == mp_payment_status or charge.mp_payment_status == _APPROVED:
                # The duplicate/retried notification MP always sends, or a
                # stale one for a charge already applied as approved.
                logger.info(
                    'Mercado Pago payment %s (Subscription %s): already processed as %s — no-op',
                    mp_payment_id, subscription.id, charge.mp_payment_status,
                )
                return 200
        else:
            charge = SubscriptionCharge(
                subscription=subscription, mp_authorized_payment_id=str(authorized_payment_id),
                mp_payment_id=mp_payment_id,
            )

        charge.mp_payment_status = mp_payment_status
        charge.amount = _decimal_or_none(authorized_payment.get('transaction_amount'))
        charge.currency = (authorized_payment.get('currency_id') or '')[:3]
        now = timezone.now()

        if _amount_mismatches(subscription, authorized_payment):
            logger.error(
                'Mercado Pago payment %s: AMOUNT/CURRENCY MISMATCH on Subscription %s — expected %s %s, '
                'MP reported %s %s (payment status=%s) — NOT applied, left as %s for manual review',
                mp_payment_id, subscription.id, subscription.amount, subscription.currency,
                authorized_payment.get('transaction_amount'), authorized_payment.get('currency_id'),
                mp_payment_status, subscription.status,
            )
            charge.outcome = SubscriptionChargeOutcome.AMOUNT_MISMATCH
        elif mp_payment_status == _APPROVED:
            charge.outcome = _apply_approved_charge(subscription, now)
        elif mp_payment_status in _FAILED:
            charge.outcome = _apply_failed_charge(subscription, now)
        else:
            charge.outcome = SubscriptionChargeOutcome.PENDING_CHARGE

        charge.save()
        subscription.last_charge_payment_id = mp_payment_id
        subscription.last_charge_status = mp_payment_status
        subscription.save()

    logger.info(
        'Mercado Pago payment %s (authorized payment %s): Subscription %s -> status=%s, outcome=%s',
        mp_payment_id, authorized_payment_id, subscription.id, subscription.status, charge.outcome,
    )
    return 200


def _decimal_or_none(value):
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except (TypeError, InvalidOperation):
        return None


def _apply_approved_charge(subscription, now):
    """A confirmed charge: grant/extend access for one paid period from
    now, end any grace, and end the member's free trial (the moment 5B-2a
    deferred it to). A CANCELLED subscription stays CANCELLED (the member
    cancelled; a charge that was already in flight still buys its
    period) — anything else becomes ACTIVE."""
    if subscription.status == SubscriptionStatus.PENDING:
        subscription.starts_at = now
    if subscription.status != SubscriptionStatus.CANCELLED:
        subscription.status = SubscriptionStatus.ACTIVE
    subscription.ends_at = paid_period_end(subscription.plan, now)
    subscription.clear_grace()
    supersede_active_trial(subscription.user, subscription, at=now)
    return SubscriptionChargeOutcome.ACTIVATED


def _apply_failed_charge(subscription, now):
    """A failed charge. Access is NOT revoked on the spot (Phase 5A rule):
      - ACTIVE, no grace yet (or a stale one on a still-running period):
        start_grace() -> access continues until grace_ends_at.
      - ACTIVE, grace still running: unchanged — a retry failing again
        must not keep pushing grace out.
      - ACTIVE, grace elapsed and the subscription is expired: PAST_DUE
        (no access).
      - PENDING (the very first charge failed): stays PENDING — never
        paid, so never had access; no grace for it.
      - PAST_DUE/CANCELLED/EXPIRED: unchanged."""
    status = subscription.status
    if status == SubscriptionStatus.PENDING:
        return SubscriptionChargeOutcome.FIRST_CHARGE_FAILED
    if status == SubscriptionStatus.PAST_DUE:
        return SubscriptionChargeOutcome.PAST_DUE
    if status != SubscriptionStatus.ACTIVE:
        return SubscriptionChargeOutcome.IGNORED
    grace_ends_at = subscription.grace_ends_at
    if grace_ends_at is not None and grace_ends_at > now:
        return SubscriptionChargeOutcome.GRACE_RUNNING
    if grace_ends_at is not None and subscription.is_expired(at=now):
        subscription.status = SubscriptionStatus.PAST_DUE
        return SubscriptionChargeOutcome.PAST_DUE
    subscription.start_grace()
    return SubscriptionChargeOutcome.GRACE_STARTED


# ── subscription_preapproval ───────────────────────────────────────────


def process_preapproval(preapproval_id):
    """The subscription's own state changed at MP. Records mp_status,
    next_payment_date and mp_payer_id. Only a CANCELLED preapproval changes
    our status; "authorized" alone grants nothing — access comes only from
    a confirmed charge (process_authorized_payment)."""
    try:
        result = _sdk().preapproval().get(preapproval_id)
        result.raise_for_status()
        preapproval = result['response']
    except Exception:
        logger.exception('Mercado Pago webhook: preapproval lookup failed for data.id=%s', preapproval_id)
        return 502

    with transaction.atomic():
        subscription = _find_subscription(
            preapproval.get('id') or preapproval_id, preapproval.get('external_reference'),
            f'preapproval {preapproval_id}',
        )
        if subscription is None:
            return 200

        mp_status = preapproval.get('status') or ''
        subscription.mp_status = mp_status
        next_payment_date = parse_datetime(preapproval.get('next_payment_date') or '')
        if next_payment_date is not None:
            subscription.next_payment_date = next_payment_date
        if preapproval.get('payer_id'):
            subscription.mp_payer_id = str(preapproval['payer_id'])
        if mp_status == 'cancelled':
            mark_subscription_cancelled(subscription)
        subscription.save()

    logger.info(
        'Mercado Pago preapproval %s: Subscription %s -> mp_status=%s, status=%s',
        preapproval_id, subscription.id, mp_status, subscription.status,
    )
    return 200


def mark_subscription_cancelled(subscription, at=None):
    """Our side of a cancelled preapproval — shared by the webhook above
    and the member's own cancellation (memberships.views.
    CancelSubscriptionView). Does NOT call save().

      - ACTIVE / PAST_DUE -> CANCELLED, ends_at UNTOUCHED: the member
        keeps access until the end of the period already paid
        (CANCELLED is an entitled status, bounded by ends_at).
      - PENDING (never paid) -> EXPIRED with ends_at = now. Never
        CANCELLED: CANCELLED is entitled, and a never-activated row has
        no ends_at, so it would grant unlimited access.
      - already CANCELLED/EXPIRED -> unchanged (idempotent).
    cancelled_at is stamped once, the first time."""
    moment = at or timezone.now()
    if subscription.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
        subscription.status = SubscriptionStatus.CANCELLED
    elif subscription.status == SubscriptionStatus.PENDING:
        subscription.status = SubscriptionStatus.EXPIRED
        subscription.ends_at = moment
    if subscription.cancelled_at is None and subscription.status in (
        SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED,
    ):
        subscription.cancelled_at = moment
