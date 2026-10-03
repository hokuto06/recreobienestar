"""
Plan upgrade: Monthly -> Annual. Upgrade only, no proration, no refunds.

Mercado Pago has no "switch plan", so an upgrade is a NEW annual preapproval
plus cancelling the monthly one — and the order is what keeps the member
from ever losing access or paying twice:

  1. The member confirms (memberships.views.UpgradeSubscriptionView). We
     create the annual Subscription A (PENDING, A.replaces = monthly M) and
     an MP preapproval whose auto_recurring.start_date is ONE DAY BEFORE M's
     next monthly charge. The member authorizes it at MP. Nothing is charged
     today (verified live 03/10/2026: a future start_date becomes a free
     trial; next_payment_date = start_date; no charge at authorization).
     M is not touched.
  2. On the start date MP charges A's first annual payment:
       - approved -> A becomes ACTIVE (webhook); its 12 months are counted
         from the END of M's paid month, so no day is lost. Then, after
         commit, M is cancelled at MP (cancel_replaced_monthly) — M keeps
         access until its own ends_at anyway (CANCELLED is entitled).
       - failed  -> A's preapproval is cancelled at MP and A is EXPIRED
         (cancel_failed_upgrade). M was never touched: MP renews the monthly
         the next day as usual, so the member just stays on Monthly.
  3. If cancelling M (or A) at MP fails, nothing is lost — the reconcile
     timer (every 30 min) retries; if M is still not cancelled within
     CANCEL_ALERT_WITHIN of its next monthly charge, Carla gets an urgent
     email to cancel it by hand (once).

WHY NOT start_date = M.ends_at: M.ends_at is M's next monthly charge PLUS
RENEWAL_MARGIN (2 days). Starting the annual there would let MP renew the
monthly first — and since M is only cancelled after A is paid, the member
would pay an extra month.

At no point does the member go without an entitled subscription: until A is
ACTIVE, M is untouched; once A is ACTIVE, M is only cancelled (still
entitled until its ends_at).
"""
import logging
from dataclasses import dataclass
from datetime import timedelta
from functools import partial

from django.db import transaction
from django.utils import timezone

from common.choices import SubscriptionStatus

from .models import MembershipPlan, Subscription, SubscriptionChargeOutcome
from .services import RENEWAL_MARGIN, billing_cadence_for_plan

logger = logging.getLogger(__name__)

MONTHLY_CADENCE = (1, 'months')
ANNUAL_CADENCE = (12, 'months')
# No upgrade this close to the monthly renewal: the annual must be
# authorized, and charged a day early, before MP renews the monthly.
MIN_DAYS_BEFORE_RENEWAL = timedelta(days=3)
# The annual's first charge, relative to the monthly's next charge.
ANNUAL_CHARGE_LEAD = timedelta(days=1)
# Alert Carla when the monthly still isn't cancelled this close to its
# next charge (the reconcile timer has been retrying until then).
CANCEL_ALERT_WITHIN = timedelta(hours=12)


def is_monthly(plan):
    return billing_cadence_for_plan(plan) == MONTHLY_CADENCE


def is_annual(plan):
    return billing_cadence_for_plan(plan) == ANNUAL_CADENCE


def annual_plan():
    for plan in MembershipPlan.objects.filter(is_active=True).order_by('price', 'id'):
        if is_annual(plan):
            return plan
    return None


def monthly_renewal_at(monthly):
    """When MP will charge the monthly next: MP's own next_payment_date
    (verified to be exact), else our ends_at minus the renewal margin."""
    if monthly.next_payment_date:
        return monthly.next_payment_date
    return monthly.ends_at - RENEWAL_MARGIN if monthly.ends_at else None


def upgrade_period_anchor(monthly):
    """Where the annual's 12 months start counting: the end of the
    monthly's paid month (= its next charge), so no paid day is lost."""
    return monthly_renewal_at(monthly)


@dataclass
class UpgradeOffer:
    """What the member's account shows. `monthly is None` -> no upgrade
    entry point at all (trial, annual, no paid subscription...)."""
    monthly: Subscription = None
    annual_plan: MembershipPlan = None
    renewal_at: object = None        # monthly's next charge = annual starts
    annual_charge_at: object = None  # annual's first charge (a day earlier)
    refusal: str = ''                # too close to renewal: why + what to do
    scheduled: Subscription = None   # an annual already authorized for this monthly
    pending: Subscription = None     # an annual created but not yet authorized

    @property
    def can_upgrade(self):
        return self.monthly is not None and not self.refusal and self.scheduled is None


def get_upgrade_offer(user, now=None):
    now = now or timezone.now()
    if user is None or not getattr(user, 'is_authenticated', False):
        return UpgradeOffer()
    plan = annual_plan()
    if plan is None:
        return UpgradeOffer()
    candidates = (
        Subscription.objects.filter(user=user, is_trial=False, status=SubscriptionStatus.ACTIVE)
        .exclude(mp_preapproval_id='').select_related('plan').order_by('-created_at')
    )
    monthly = next(
        (s for s in candidates if is_monthly(s.plan) and s.is_active(at=now) and s.grace_ends_at is None),
        None,
    )
    if monthly is None:
        return UpgradeOffer()
    renewal_at = monthly_renewal_at(monthly)
    if renewal_at is None:
        return UpgradeOffer()
    offer = UpgradeOffer(
        monthly=monthly, annual_plan=plan, renewal_at=renewal_at,
        annual_charge_at=renewal_at - ANNUAL_CHARGE_LEAD,
    )
    existing = (
        Subscription.objects.filter(replaces=monthly, status=SubscriptionStatus.PENDING)
        .exclude(mp_preapproval_id='').order_by('-created_at').first()
    )
    if existing is not None and existing.mp_status == 'authorized':
        offer.scheduled = existing
        offer.annual_charge_at = existing.starts_at
        return offer
    offer.pending = existing
    if renewal_at - now < MIN_DAYS_BEFORE_RENEWAL:
        day = timezone.localtime(renewal_at).strftime('%d/%m/%Y')
        offer.refusal = (
            f'Tu renovación mensual es el {day}. Para no cobrarte dos veces, el cambio al plan '
            f'anual tiene que quedar listo unos días antes, así que ahora no se puede: podés '
            f'pasarte al anual después del {day}.'
        )
    return offer


# ── Follow-ups (MP calls, run after commit, never raise) ───────────────


def _after_commit(func, *args):
    try:
        transaction.on_commit(partial(func, *args), robust=True)
    except Exception:
        logger.exception('Could not schedule %s%r', func.__name__, args)


def schedule_replaced_monthly_cancellation(annual_id):
    """Call from the transaction that ACTIVATES an upgrade's annual."""
    _after_commit(cancel_replaced_monthly, annual_id)


def schedule_failed_upgrade_cancellation(annual_id):
    """Call from the transaction that records an upgrade's failed first charge."""
    _after_commit(cancel_failed_upgrade, annual_id)


def _cancel_at_mp(preapproval_id):
    from .webhooks import _sdk

    result = _sdk().preapproval().update(preapproval_id, {'status': 'cancelled'})
    result.raise_for_status()


def cancel_replaced_monthly(annual_id):
    """Cancels the monthly an ACTIVE annual replaces. Returns 'cancelled',
    'noop' (nothing to do) or 'failed' (MP refused; retried by the
    reconcile timer). Never raises."""
    from .webhooks import mark_subscription_cancelled
    from .views import CANCELLABLE_STATUSES

    try:
        with transaction.atomic():
            annual = Subscription.objects.select_for_update().filter(pk=annual_id).first()
            if annual is None or annual.replaces_id is None or annual.status != SubscriptionStatus.ACTIVE:
                return 'noop'
            monthly = Subscription.objects.select_for_update().get(pk=annual.replaces_id)
            if monthly.status not in CANCELLABLE_STATUSES or not monthly.mp_preapproval_id:
                return 'noop'
            try:
                _cancel_at_mp(monthly.mp_preapproval_id)
            except Exception:
                logger.exception(
                    'Upgrade: cancelling monthly Subscription %s (preapproval %s) at MP failed — '
                    'will be retried by reconcile_subscription_charges',
                    monthly.id, monthly.mp_preapproval_id,
                )
                return 'failed'
            mark_subscription_cancelled(monthly)
            monthly.mp_status = 'cancelled'
            monthly.save(update_fields=['status', 'ends_at', 'cancelled_at', 'mp_status', 'updated_at'])
        logger.info('Upgrade: monthly Subscription %s cancelled, replaced by annual %s', monthly.id, annual_id)
        return 'cancelled'
    except Exception:
        logger.exception('Upgrade: cancel_replaced_monthly(%s) crashed', annual_id)
        return 'failed'


def cancel_failed_upgrade(annual_id):
    """An upgrade's annual whose first charge failed: cancel its preapproval
    (so MP can't charge it later, after the monthly already renewed) and
    expire it; tell the member they stay on Monthly. Returns 'cancelled',
    'noop' or 'failed' (retried by the reconcile timer). Never raises."""
    from common.notifications import schedule_upgrade_failed_notification
    from .webhooks import mark_subscription_cancelled

    try:
        with transaction.atomic():
            annual = Subscription.objects.select_for_update().filter(pk=annual_id).first()
            if (
                annual is None or annual.replaces_id is None
                or annual.status != SubscriptionStatus.PENDING or not annual.mp_preapproval_id
            ):
                return 'noop'
            try:
                _cancel_at_mp(annual.mp_preapproval_id)
            except Exception:
                logger.exception(
                    'Upgrade: cancelling failed annual Subscription %s (preapproval %s) at MP failed — '
                    'will be retried by reconcile_subscription_charges',
                    annual.id, annual.mp_preapproval_id,
                )
                return 'failed'
            mark_subscription_cancelled(annual)  # PENDING -> EXPIRED, never CANCELLED
            annual.mp_status = 'cancelled'
            annual.save(update_fields=['status', 'ends_at', 'cancelled_at', 'mp_status', 'updated_at'])
            schedule_upgrade_failed_notification(annual.id)
        logger.info('Upgrade: annual Subscription %s cancelled after its first charge failed', annual_id)
        return 'cancelled'
    except Exception:
        logger.exception('Upgrade: cancel_failed_upgrade(%s) crashed', annual_id)
        return 'failed'


def retry_upgrade_followups(now=None, stdout=None):
    """For the reconcile timer: retries the two MP cancellations above and
    alerts Carla (once) when a replaced monthly is still not cancelled
    close to its next charge. Bounded to upgrades activated/failed in the
    last 40 days. Returns a summary dict."""
    from common.notifications import schedule_upgrade_cancel_alert

    now = now or timezone.now()
    since = now - timedelta(days=40)
    summary = {'monthly_cancelled': 0, 'monthly_failed': 0, 'alerts': 0,
               'failed_annual_cancelled': 0, 'failed_annual_failed': 0}

    stuck_monthlies = (
        Subscription.objects.filter(
            replaced_by__status=SubscriptionStatus.ACTIVE, replaced_by__updated_at__gte=since,
            status__in=(SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE),
        ).exclude(mp_preapproval_id='').distinct().order_by('id')
    )
    for monthly in stuck_monthlies:
        annual = monthly.replaced_by.filter(status=SubscriptionStatus.ACTIVE).order_by('-id').first()
        outcome = cancel_replaced_monthly(annual.id)
        if outcome == 'cancelled':
            summary['monthly_cancelled'] += 1
            continue
        if outcome != 'failed':
            continue
        summary['monthly_failed'] += 1
        renewal_at = monthly_renewal_at(monthly)
        if (
            monthly.upgrade_cancel_alerted_at is None
            and renewal_at is not None and renewal_at - now <= CANCEL_ALERT_WITHIN
        ):
            with transaction.atomic():
                locked = Subscription.objects.select_for_update().get(pk=monthly.pk)
                if locked.upgrade_cancel_alerted_at is None:
                    locked.upgrade_cancel_alerted_at = now
                    locked.save(update_fields=['upgrade_cancel_alerted_at', 'updated_at'])
                    schedule_upgrade_cancel_alert(locked.pk)
                    summary['alerts'] += 1

    failed_annuals = (
        Subscription.objects.filter(
            replaces__isnull=False, status=SubscriptionStatus.PENDING, updated_at__gte=since,
            charges__outcome=SubscriptionChargeOutcome.FIRST_CHARGE_FAILED,
        ).exclude(mp_preapproval_id='').distinct().order_by('id')
    )
    for annual in failed_annuals:
        outcome = cancel_failed_upgrade(annual.id)
        if outcome == 'cancelled':
            summary['failed_annual_cancelled'] += 1
        elif outcome == 'failed':
            summary['failed_annual_failed'] += 1
    return summary
