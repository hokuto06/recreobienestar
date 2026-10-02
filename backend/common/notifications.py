"""
"You made a sale" emails to Carla — one per completed OfferingPurchase and
one per confirmed subscription charge (first activation or renewal).

The payment code only ever calls the two schedule_* functions, from the
single point where each sale is settled:

  payments.services.apply_payment_to_purchase   — purchase -> COMPLETED
  memberships.webhooks.process_authorized_payment — charge -> ACTIVATED

AN EMAIL CAN NEVER BREAK A SALE. Three independent layers:
  1. schedule_* never raises (its body is wrapped in try/except), so the
     call sitting inside the payment's atomic block can't abort it.
  2. The email isn't sent there at all: transaction.on_commit defers it
     until AFTER the sale's transaction has committed — by the time SMTP is
     touched, the purchase/subscription is already durably saved, and a
     transaction that rolls back never sends anything.
  3. The deferred send catches and logs every exception itself, and is
     registered with robust=True so Django would also log-and-swallow
     anything that still escaped — an SMTP failure never reaches the
     webhook's response.
A failed email is logged (logger.exception) and not retried; the sale
stands either way.

NO DUPLICATES: neither call site is reachable twice for the same sale.
Each sits after the payment code's own idempotency guard, which runs under
a row lock (select_for_update): an already-COMPLETED purchase, or a charge
already recorded as approved, returns early before the hook. So only the
one notification that actually performs the transition schedules an email;
MP's retries/duplicates don't. No extra state needed.

Content is deliberately minimal: what was sold, amount + currency, buyer
name and email, when, and our/Mercado Pago's reference numbers so Carla
can find it in the Admin or in MP. No tokens, card or payer data.
"""
import logging
from decimal import Decimal
from email.utils import parseaddr
from functools import partial

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ── Entry points for the payment code ──────────────────────────────────


def schedule_offering_sale_notification(purchase_id):
    """Call from inside the transaction that marks the purchase COMPLETED."""
    _after_commit(notify_offering_sale, purchase_id)


def schedule_subscription_charge_notification(charge_id):
    """Call from inside the transaction that records an ACTIVATED charge."""
    _after_commit(notify_subscription_charge, charge_id)


def _after_commit(func, object_id):
    try:
        transaction.on_commit(partial(func, object_id), robust=True)
    except Exception:
        logger.exception(
            'Could not schedule sale notification %s(%s) — the sale itself is unaffected',
            func.__name__, object_id,
        )


# ── Senders (run after commit; never raise) ────────────────────────────


def notify_offering_sale(purchase_id):
    try:
        from payments.models import OfferingPurchase

        purchase = (
            OfferingPurchase.objects.select_related('offering', 'user', 'user__profile')
            .get(pk=purchase_id)
        )
        amount = purchase.amount if purchase.amount is not None else purchase.offering.price
        currency = purchase.currency or purchase.offering.currency
        lines = [
            'Se completó una venta en Recreo Bienestar.',
            '',
            f'Qué se vendió: {purchase.offering.name} (propuesta, pago único)',
            f'Monto: {_money(amount, currency)}',
            f'Comprador/a: {_buyer(purchase.user)}',
            f'Fecha: {_when(purchase.updated_at)}',
            f'Referencia: compra #{purchase.pk}{_mp_ref(purchase.mp_payment_id)}',
        ]
        _send(f'Nueva venta: {purchase.offering.name} — {_money(amount, currency)}', lines)
    except Exception:
        logger.exception(
            'Sale notification for OfferingPurchase %s failed to send — the sale itself is unaffected',
            purchase_id,
        )


def notify_subscription_charge(charge_id):
    try:
        from memberships.models import SubscriptionCharge, SubscriptionChargeOutcome

        charge = (
            SubscriptionCharge.objects
            .select_related('subscription__plan', 'subscription__user', 'subscription__user__profile')
            .get(pk=charge_id)
        )
        subscription = charge.subscription
        is_first = not subscription.charges.filter(
            outcome=SubscriptionChargeOutcome.ACTIVATED,
        ).exclude(pk=charge.pk).exists()
        kind = 'Nueva suscripción' if is_first else 'Renovación de suscripción'
        amount = charge.amount if charge.amount is not None else subscription.amount
        currency = charge.currency or subscription.currency
        lines = [
            f'{kind} en Recreo Bienestar: se acreditó un cobro.',
            '',
            f'Qué se vendió: {subscription.plan.name} (membresía)',
            f'Monto: {_money(amount, currency)}',
            f'Comprador/a: {_buyer(subscription.user)}',
            f'Fecha: {_when(charge.updated_at)}',
        ]
        if subscription.ends_at:
            lines.append(f'Acceso pago hasta: {_when(subscription.ends_at)}')
        lines.append(f'Referencia: suscripción #{subscription.pk}{_mp_ref(charge.mp_payment_id)}')
        _send(f'{kind}: {subscription.plan.name} — {_money(amount, currency)}', lines)
    except Exception:
        logger.exception(
            'Sale notification for SubscriptionCharge %s failed to send — the sale itself is unaffected',
            charge_id,
        )


# ── Helpers ────────────────────────────────────────────────────────────


def sale_notification_recipient():
    """Carla's address: SiteSettings.contact_email (editable in the Admin)
    if set, else settings.SALE_NOTIFICATION_EMAIL, else the address in
    DEFAULT_FROM_EMAIL (the site's own Gmail inbox)."""
    from site_content.models import SiteSettings

    return (
        SiteSettings.load().contact_email
        or settings.SALE_NOTIFICATION_EMAIL
        or parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    )


def _send(subject, lines):
    body = '\n'.join(['Hola,', ''] + lines + ['', '— Recreo Bienestar (aviso automático)', ''])
    send_mail(subject, body, None, [sale_notification_recipient()], fail_silently=False)


def _money(amount, currency):
    if amount is None:
        return f'(monto no registrado) {currency}'.strip()
    # Argentine format: 55.000,00
    text = f'{Decimal(amount):,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    return f'{text} {currency}'.strip()


def _buyer(user):
    profile = getattr(user, 'profile', None)
    name = (profile.display_name if profile else '') or user.get_full_name() or user.get_username()
    return f'{name} <{user.email}>' if user.email else name


def _when(moment):
    return timezone.localtime(moment).strftime('%d/%m/%Y %H:%M') + ' (hora de Argentina)'


def _mp_ref(mp_payment_id):
    return f' · pago de Mercado Pago {mp_payment_id}' if mp_payment_id else ''
