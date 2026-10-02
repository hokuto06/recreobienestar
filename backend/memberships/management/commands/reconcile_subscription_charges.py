"""
Safety net for subscription charges whose Mercado Pago notification we
never managed to process — the 02/10/2026 incident: MP notified charge
7032483746 before its own API could return it, our lookup got a 404, we
answered 502 and MP never retried. The member had paid, stayed PENDING
with no access, and no emails went out.

Finds paid-plan Subscriptions that are still PENDING, have a preapproval,
were created within the last --days (default 7), and have no ACTIVATED
charge. For each, asks MP for the preapproval's charges
(GET /authorized_payments/search?preapproval_id=...) and feeds every
approved one to memberships.webhooks.process_authorized_payment — the very
function the webhook uses, so it re-fetches the charge from MP, checks the
amount against the signup snapshot, locks the rows, records the
SubscriptionCharge, activates, and schedules both emails after commit.
Nothing here decides access on its own.

Safe to re-run (it's meant for cron, every ~15 minutes):
  - an activated subscription is no longer PENDING-without-an-activated-
    charge, so it's never selected again;
  - even if it were, process_authorized_payment's own guard turns an
    already-approved payment id into a no-op — no second activation, no
    second email;
  - it never creates a Subscription and never activates without an
    approved charge confirmed by MP.

Usage:
    python manage.py reconcile_subscription_charges              # apply
    python manage.py reconcile_subscription_charges --dry-run    # report only, no writes
    python manage.py reconcile_subscription_charges --days 30    # wider window

Output: one line per candidate, then a one-line summary, each prefixed with
an ISO timestamp, e.g.
    2026-10-02T12:00:01+00:00 sub #12 (pre d5b7…): approved charge 7032483746 -> applied (status=active)
    2026-10-02T12:00:01+00:00 reconcile: 1 candidate(s), 1 activated, 0 without an approved charge, 0 errors
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.choices import SubscriptionStatus
from memberships.models import Subscription, SubscriptionChargeOutcome
from memberships.webhooks import _APPROVED, _sdk, process_authorized_payment

DEFAULT_WINDOW_DAYS = 7


class Command(BaseCommand):
    help = 'Activates PENDING subscriptions whose approved Mercado Pago charge was never processed.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would be applied; change nothing.')
        parser.add_argument(
            '--days', type=int, default=DEFAULT_WINDOW_DAYS,
            help=f'Only subscriptions created within this many days (default {DEFAULT_WINDOW_DAYS}).',
        )

    def _line(self, text):
        self.stdout.write(f'{timezone.now().isoformat(timespec="seconds")} {text}')

    def handle(self, *args, dry_run=False, days=DEFAULT_WINDOW_DAYS, **options):
        since = timezone.now() - timedelta(days=days)
        candidates = (
            Subscription.objects
            .filter(status=SubscriptionStatus.PENDING, is_trial=False, created_at__gte=since)
            .exclude(mp_preapproval_id='')
            .exclude(charges__outcome=SubscriptionChargeOutcome.ACTIVATED)
            .distinct().order_by('id')
        )
        activated = no_charge = errors = 0
        total = 0
        for subscription in candidates:
            total += 1
            label = f'sub #{subscription.id} (pre {subscription.mp_preapproval_id[:4]}…)'
            try:
                approved = self._approved_charges(subscription.mp_preapproval_id)
            except Exception as exc:
                errors += 1
                self._line(f'{label}: MP search failed — {type(exc).__name__}: {exc}')
                continue
            if not approved:
                no_charge += 1
                self._line(f'{label}: no approved charge at MP — left pending')
                continue
            for charge_id in approved:
                if dry_run:
                    self._line(f'{label}: approved charge {charge_id} -> would apply (dry run)')
                    continue
                code = process_authorized_payment(charge_id)
                subscription.refresh_from_db()
                self._line(f'{label}: approved charge {charge_id} -> applied (http={code}, status={subscription.status})')
                if code != 200:
                    errors += 1
            if not dry_run and subscription.status != SubscriptionStatus.PENDING:
                activated += 1
        verb = 'would activate' if dry_run else 'activated'
        self._line(
            f'reconcile{" (dry run)" if dry_run else ""}: {total} candidate(s), '
            f'{activated if not dry_run else total - no_charge - errors} {verb}, '
            f'{no_charge} without an approved charge, {errors} errors'
        )

    def _approved_charges(self, preapproval_id):
        """Ids of the preapproval's authorized payments whose payment MP
        reports as approved, oldest first. Only used to pick WHICH ids to
        hand to process_authorized_payment — that function re-fetches each
        one and makes the actual decision."""
        result = _sdk().invoice().search(filters={'preapproval_id': preapproval_id})
        result.raise_for_status()
        body = result['response'] or {}
        found = body.get('results') or []
        approved = [
            item for item in found
            if (item.get('payment') or {}).get('status') == _APPROVED and item.get('id')
        ]
        approved.sort(key=lambda item: (item.get('date_created') or '', str(item['id'])))
        return [str(item['id']) for item in approved]
