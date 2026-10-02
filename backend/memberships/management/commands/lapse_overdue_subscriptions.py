"""
Daily: ends the grace period of subscriptions whose failed renewal was
never paid, and tells the member and Carla.

When a renewal charge fails, the webhook keeps the subscription ACTIVE and
stamps grace_ends_at (access continues). If a later failure arrives after
grace ran out, the webhook itself flips it to PAST_DUE — but if Mercado
Pago simply stops retrying, nothing ever arrives, and the row would sit at
ACTIVE forever (access already ends by date, but nobody is told). This
command closes that gap.

Selects ACTIVE subscriptions whose grace_ends_at has passed within the last
--days (default 30), locks each row, re-checks it, and — only if it's
still ACTIVE, grace has elapsed and the subscription is expired (no
successful charge since: an approved charge clears grace_ends_at) — sets
PAST_DUE and schedules the member's and Carla's lapse emails after commit.

ACCESS: unchanged by this command. Such a subscription is already expired
by date (is_active() is False), PAST_DUE grants nothing either, and it's
exactly the state the webhook's own rule produces on the next failed
charge; an approved charge later reactivates PAST_DUE and ACTIVE the same
way. Tests pin this.

Idempotent: a PAST_DUE row is never selected again, and the emails are
scheduled only on the ACTIVE -> PAST_DUE transition. Older lapses (grace
ended more than --days ago) are counted but left alone: they're already
without access, and a months-late "you lost access" email helps nobody.

Deliberately does NOT relabel other ended subscriptions as EXPIRED: for an
ACTIVE subscription past its end date with no grace stamped, a late failed
charge starts a grace period (access) while an EXPIRED one is ignored — so
that relabelling would change access. See ARCHITECTURE.md.

Usage:
    python manage.py lapse_overdue_subscriptions             # apply
    python manage.py lapse_overdue_subscriptions --dry-run   # report only
    python manage.py lapse_overdue_subscriptions --days 60

Output: one timestamped line per subscription, then a summary line.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from common.choices import SubscriptionStatus
from common.notifications import schedule_lapse_notification
from memberships.models import Subscription

DEFAULT_WINDOW_DAYS = 30


class Command(BaseCommand):
    help = 'Flips ACTIVE subscriptions whose grace period ran out to PAST_DUE and notifies member + Carla.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would change; change nothing.')
        parser.add_argument(
            '--days', type=int, default=DEFAULT_WINDOW_DAYS,
            help=f'Only grace periods that ended within this many days (default {DEFAULT_WINDOW_DAYS}).',
        )

    def _line(self, text):
        self.stdout.write(f'{timezone.now().isoformat(timespec="seconds")} {text}')

    def handle(self, *args, dry_run=False, days=DEFAULT_WINDOW_DAYS, **options):
        now = timezone.now()
        since = now - timedelta(days=days)
        overdue = Subscription.objects.filter(
            status=SubscriptionStatus.ACTIVE, grace_ends_at__isnull=False, grace_ends_at__lte=now,
        )
        candidates = overdue.filter(grace_ends_at__gt=since).select_related('plan').order_by('grace_ends_at', 'id')
        older = overdue.filter(grace_ends_at__lte=since).count()

        lapsed = skipped = 0
        for candidate in candidates:
            label = f'sub #{candidate.id} ({candidate.plan.name}, grace ended {timezone.localtime(candidate.grace_ends_at):%d/%m/%Y %H:%M})'
            if dry_run:
                if candidate.is_expired(at=now):
                    lapsed += 1
                    self._line(f'{label}: would set PAST_DUE and notify member + Carla (dry run)')
                else:
                    skipped += 1
                    self._line(f'{label}: still within its paid period — would be left alone (dry run)')
                continue
            with transaction.atomic():
                subscription = Subscription.objects.select_for_update().get(pk=candidate.pk)
                if not (
                    subscription.status == SubscriptionStatus.ACTIVE
                    and subscription.grace_ends_at is not None
                    and subscription.grace_ends_at <= now
                    and subscription.is_expired(at=now)
                ):
                    skipped += 1
                    self._line(f'{label}: changed meanwhile or still within its paid period — left alone')
                    continue
                subscription.status = SubscriptionStatus.PAST_DUE
                subscription.save(update_fields=['status', 'updated_at'])
                schedule_lapse_notification(subscription.pk)
            lapsed += 1
            self._line(f'{label}: set PAST_DUE, member + Carla notified')

        verb = 'would lapse' if dry_run else 'lapsed'
        self._line(
            f'lapse{" (dry run)" if dry_run else ""}: {lapsed + skipped} candidate(s), {lapsed} {verb}, '
            f'{skipped} left alone, {older} older than {days} days untouched'
        )
