"""
Sale emails, two per sale:
  - to Carla: "you made a sale" (one per completed OfferingPurchase and one
    per confirmed subscription charge — first activation or renewal);
  - to the buyer: a confirmation with how to access what they bought (a
    welcome for a subscription's first charge, shorter copy for renewals).
The two are separate after-commit callbacks: one failing never stops the
other.

Plus the dunning emails (same machinery, same guarantees):
  - grace started (a renewal charge failed): to the member — they keep
    access until grace_ends_at, update the card AT MERCADO PAGO;
  - access lapsed (grace ran out, ACTIVE -> PAST_DUE): to the member AND
    to Carla, as two independent callbacks.
Both fire only on the state TRANSITION, never on a repeat (see the callers
in memberships/webhooks.py and lapse_overdue_subscriptions).

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
stands either way. A buyer with no email address is skipped and logged.

NO DUPLICATES: neither call site is reachable twice for the same sale.
Each sits after the payment code's own idempotency guard, which runs under
a row lock (select_for_update): an already-COMPLETED purchase, or a charge
already recorded as approved, returns early before the hook. So only the
one notification that actually performs the transition schedules an email;
MP's retries/duplicates don't. No extra state needed.

Content is deliberately minimal: what was sold, amount + currency, buyer
name and email (Carla's copy only), when, and our/Mercado Pago's reference
numbers. No tokens, card or payer data, no preference id. Links in the
buyer's email are absolute (settings.SITE_URL) — they're opened from an
email client, not the site.
"""
import logging
from decimal import Decimal
from email.utils import parseaddr
from functools import partial

from django.conf import settings
from django.core.mail import EmailMessage, send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


# ── Entry points for the payment code ──────────────────────────────────


def schedule_offering_sale_notification(purchase_id):
    """Call from inside the transaction that marks the purchase COMPLETED.
    Schedules Carla's notification and the buyer's confirmation as two
    independent callbacks."""
    _after_commit(notify_offering_sale, purchase_id)
    _after_commit(notify_buyer_offering_purchase, purchase_id)


def schedule_subscription_charge_notification(charge_id):
    """Call from inside the transaction that records an ACTIVATED charge.
    Schedules Carla's notification and the subscriber's confirmation as
    two independent callbacks."""
    _after_commit(notify_subscription_charge, charge_id)
    _after_commit(notify_buyer_subscription_charge, charge_id)


def schedule_grace_started_notification(subscription_id):
    """Call from inside the transaction that stamps a NEW grace period."""
    _after_commit(notify_member_grace_started, subscription_id)


def schedule_lapse_notification(subscription_id):
    """Call from inside the transaction that flips ACTIVE -> PAST_DUE.
    Member's notice and Carla's, as two independent callbacks."""
    _after_commit(notify_member_access_lapsed, subscription_id)
    _after_commit(notify_carla_access_lapsed, subscription_id)


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
        kind = 'Nueva suscripción' if _is_first_charge(charge) else 'Renovación de suscripción'
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


# ── Buyer confirmations (run after commit; never raise) ────────────────


def notify_buyer_offering_purchase(purchase_id):
    try:
        from payments.models import OfferingPurchase

        purchase = (
            OfferingPurchase.objects.select_related('offering', 'user', 'user__profile')
            .get(pk=purchase_id)
        )
        if not _has_email(purchase.user, f'OfferingPurchase {purchase_id}'):
            return
        offering = purchase.offering
        amount = purchase.amount if purchase.amount is not None else offering.price
        currency = purchase.currency or offering.currency
        how_to_access = [
            'Para empezar, entrá a tu cuenta: los videos están en «Disponibles para vos».',
            _url('accounts:dashboard'),
            '',
            'También los encontrás en la videoteca:',
            _url('catalog:video_library'),
            '',
        ]
        if offering.deliverable:
            # The protected download view (purchase-checked, asks to log in
            # if needed) — never a direct file URL. Offerings without a file
            # keep exactly the copy above.
            download = [
                'Descargá tu PDF acá (si no ingresaste, te va a pedir que entres con tu cuenta):',
                f'{settings.SITE_URL}{reverse("site_content:offering_download", args=[offering.slug])}',
                '',
                'También lo tenés siempre en tu cuenta, en «Tus descargas»:',
                _url('accounts:dashboard'),
                '',
            ]
            how_to_access = download + (how_to_access if offering.videos.exists() else [])
        lines = [
            f'Hola {_first_name(purchase.user)},',
            '',
            f'¡Gracias por tu compra! Ya tenés acceso a «{offering.name}».',
            '',
            *how_to_access,
            'Detalle de tu compra',
            f'· {offering.name}',
            f'· {_money(amount, currency)}',
            f'· {_when(purchase.updated_at)}',
        ]
        if purchase.mp_payment_id:
            lines.append(f'· Referencia de pago (Mercado Pago): {purchase.mp_payment_id}')
        lines += _signature()
        _send_to_buyer(purchase.user, f'Tu compra en Recreo Bienestar: {offering.name}', lines)
    except Exception:
        logger.exception(
            'Buyer confirmation for OfferingPurchase %s failed to send — the sale itself is unaffected',
            purchase_id,
        )


def notify_buyer_subscription_charge(charge_id):
    try:
        from common.choices import SubscriptionStatus
        from memberships.models import SubscriptionCharge
        from memberships.services import billing_cadence_for_plan

        charge = (
            SubscriptionCharge.objects
            .select_related('subscription__plan', 'subscription__user', 'subscription__user__profile')
            .get(pk=charge_id)
        )
        subscription = charge.subscription
        user = subscription.user
        if not _has_email(user, f'SubscriptionCharge {charge_id}'):
            return
        plan = subscription.plan
        amount = charge.amount if charge.amount is not None else subscription.amount
        currency = charge.currency or subscription.currency
        details = [
            f'· Plan: {plan.name}',
            f'· Monto: {_money(amount, currency)}',
            f'· Fecha del cobro: {_when(charge.updated_at)}',
        ]
        if subscription.ends_at:
            details.append(f'· Acceso pago hasta: {_when(subscription.ends_at)}')
        if charge.mp_payment_id:
            details.append(f'· Referencia de pago (Mercado Pago): {charge.mp_payment_id}')

        cancelled = subscription.status == SubscriptionStatus.CANCELLED
        if cancelled:
            # A charge already in flight when they cancelled: it still buys
            # the period, but nothing renews — never tell them it will.
            renewal = [
                'Tu suscripción está cancelada, así que no va a haber nuevos cobros: '
                'mantenés el acceso hasta la fecha de arriba.',
            ]
        else:
            renewal = [
                f'Tu membresía se renueva automáticamente {_cadence(billing_cadence_for_plan(plan))} '
                'con un cobro de Mercado Pago, hasta que la canceles. Podés cancelarla cuando '
                'quieras desde:',
                _url('memberships:mi_suscripcion'),
            ]

        if _is_first_charge(charge):
            subject = f'¡Te damos la bienvenida a {plan.name}!'
            lines = [
                f'Hola {_first_name(user)},',
                '',
                f'¡Qué alegría que te sumes! Tu membresía {plan.name} ya está activa.',
                '',
                'Ya podés ver todos los videos de tu plan en la videoteca:',
                _url('catalog:video_library'),
                '',
                'Y en tu cuenta tenés tus videos disponibles y tu progreso:',
                _url('accounts:dashboard'),
                '',
                'Detalle',
                *details,
                '',
                *renewal,
            ]
        else:
            subject = f'Renovamos tu membresía {plan.name}'
            lines = [
                f'Hola {_first_name(user)},',
                '',
                f'Se acreditó la renovación de tu membresía {plan.name}. '
                'Gracias por seguir practicando conmigo.',
                '',
                *details,
                '',
                *(renewal[:1] if cancelled else []),
                f'Tus videos te esperan en {_url("catalog:video_library")}',
                f'Tu suscripción: {_url("memberships:mi_suscripcion")}' if cancelled
                else f'Para ver o cancelar tu suscripción: {_url("memberships:mi_suscripcion")}',
            ]
        lines += _signature()
        _send_to_buyer(user, subject, lines)
    except Exception:
        logger.exception(
            'Buyer confirmation for SubscriptionCharge %s failed to send — the sale itself is unaffected',
            charge_id,
        )


# ── Dunning (run after commit; never raise) ────────────────────────────


def _subscription(subscription_id):
    from memberships.models import Subscription

    return (
        Subscription.objects.select_related('plan', 'user', 'user__profile')
        .get(pk=subscription_id)
    )


def notify_member_grace_started(subscription_id):
    try:
        subscription = _subscription(subscription_id)
        user = subscription.user
        if not _has_email(user, f'grace notice for Subscription {subscription_id}'):
            return
        plan = subscription.plan
        amount = _money(subscription.amount if subscription.amount is not None else plan.price,
                        subscription.currency or plan.currency)
        until = _day(subscription.grace_ends_at) if subscription.grace_ends_at else 'dentro de unos días'
        lines = [
            f'Hola {_first_name(user)},',
            '',
            f'Mercado Pago no pudo cobrar la renovación de tu membresía {plan.name} ({amount}). '
            f'No te preocupes: seguís teniendo acceso a todos tus videos hasta el {until}.',
            '',
            'Para que no se corte, revisá el medio de pago en tu cuenta de Mercado Pago: la tarjeta '
            'se maneja ahí, no en Recreo Bienestar. Entrá a Mercado Pago (la app o la web), buscá tu '
            'suscripción a Recreo Bienestar y actualizá la tarjeta o el medio de pago.',
            '',
            'Mercado Pago va a volver a intentar el cobro automáticamente en los próximos días. '
            'Si se acredita, no tenés que hacer nada más.',
            '',
            'Podés ver el estado de tu membresía (y cancelarla, si preferís) acá:',
            _url('memberships:mi_suscripcion'),
            *_signature(),
        ]
        _send_to_buyer(user, f'No pudimos cobrar tu membresía {plan.name}', lines)
    except Exception:
        logger.exception(
            'Grace notice for Subscription %s failed to send — the subscription itself is unaffected',
            subscription_id,
        )


def notify_member_access_lapsed(subscription_id):
    try:
        subscription = _subscription(subscription_id)
        user = subscription.user
        if not _has_email(user, f'lapse notice for Subscription {subscription_id}'):
            return
        plan = subscription.plan
        lines = [
            f'Hola {_first_name(user)},',
            '',
            f'Como no se pudo cobrar la renovación de tu membresía {plan.name}, quedó suspendida '
            'y ya no tenés acceso a los videos del plan.',
            '',
            'Si el cobro se acredita más adelante, tu acceso se reactiva solo. Y si querés volver '
            'eligiendo un plan de nuevo, me encantaría seguir acompañándote:',
            f'{settings.SITE_URL}/#columna-sana',
            '',
            'Tu cuenta:',
            _url('accounts:dashboard'),
            *_signature(),
        ]
        _send_to_buyer(user, f'Tu membresía {plan.name} quedó suspendida', lines)
    except Exception:
        logger.exception(
            'Lapse notice to member for Subscription %s failed to send — the subscription itself is '
            'unaffected', subscription_id,
        )


def notify_carla_access_lapsed(subscription_id):
    try:
        subscription = _subscription(subscription_id)
        plan = subscription.plan
        amount = _money(subscription.amount if subscription.amount is not None else plan.price,
                        subscription.currency or plan.currency)
        name = _buyer(subscription.user)
        lines = [
            'Se suspendió una membresía: no se pudo cobrar la renovación y terminó el período de gracia.',
            '',
            f'Miembro: {name}',
            f'Plan: {plan.name}',
            f'Monto: {amount}',
        ]
        if subscription.grace_ends_at:
            lines.append(f'Gracia hasta: {_when(subscription.grace_ends_at)}')
        lines += [
            f'Referencia: suscripción #{subscription.pk}',
            '',
            'Quizás quieras escribirle para ver si necesita una mano con el pago.',
        ]
        _send(f'Membresía suspendida por falta de pago: {plan.name} — {name}', lines)
    except Exception:
        logger.exception(
            'Lapse notice to Carla for Subscription %s failed to send — the subscription itself is '
            'unaffected', subscription_id,
        )


# ── Helpers ────────────────────────────────────────────────────────────


def _is_first_charge(charge):
    """True when no OTHER charge of the same subscription was ACTIVATED —
    i.e. this charge started the subscription; anything after is a
    renewal. Per subscription: a member who cancels and later subscribes
    again starts a new Subscription row and gets the welcome again."""
    from memberships.models import SubscriptionChargeOutcome

    return not charge.subscription.charges.filter(
        outcome=SubscriptionChargeOutcome.ACTIVATED,
    ).exclude(pk=charge.pk).exists()


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


def _has_email(user, context):
    if user.email:
        return True
    logger.warning('Buyer confirmation for %s skipped: user %s has no email address', context, user.pk)
    return False


def _send_to_buyer(user, subject, lines):
    # Replies go to Carla's own address (SiteSettings.contact_email, ...),
    # not just the sending Gmail account.
    EmailMessage(
        subject, '\n'.join(lines) + '\n', None, [user.email],
        reply_to=[sale_notification_recipient()],
    ).send(fail_silently=False)


def _first_name(user):
    profile = getattr(user, 'profile', None)
    name = (profile.display_name if profile else '') or user.first_name or user.get_username()
    return name.split()[0] if name.strip() else name


def _url(name):
    return f'{settings.SITE_URL}{reverse(name)}'


def _cadence(cadence):
    if cadence == (1, 'months'):
        return 'cada mes'
    if cadence == (12, 'months'):
        return 'cada año'
    return 'al final de cada período'


def _signature():
    return [
        '',
        'Si tenés cualquier duda, respondé este mail y te contesto.',
        '',
        'Un abrazo,',
        'Carla — Recreo Bienestar',
    ]


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


def _day(moment):
    return timezone.localtime(moment).strftime('%d/%m/%Y')


def _mp_ref(mp_payment_id):
    return f' · pago de Mercado Pago {mp_payment_id}' if mp_payment_id else ''
