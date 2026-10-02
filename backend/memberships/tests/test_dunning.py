"""Dunning: a failed renewal emails the member once per grace period; when
grace runs out with no payment (webhook or the daily
lapse_overdue_subscriptions command) the subscription goes PAST_DUE and the
member and Carla are told — once. None of it changes who can access what.

MP SDK mocked, locmem outbox, never real SMTP."""
import smtplib
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from catalog.models import Video
from common.choices import SubscriptionStatus
from memberships.models import Subscription, SubscriptionCharge, SubscriptionChargeOutcome
from memberships.services import can_access_video
from memberships.tests.test_subscription_webhooks import SUB_SDK, _Fixtures
from memberships.webhooks import _apply_approved_charge, _apply_failed_charge
from payments.tests.test_checkout import FakeMPResponse
from payments.tests.test_webhook import WEBHOOK_SECRET, WEBHOOK_URL, _signature_header
from site_content.models import SiteSettings

CARLA = 'carla@example.com'
MEMBER = 'socia@example.com'
_MODULE = 'memberships.tests.test_dunning'


class FailForMemberBackend(LocmemBackend):
    def send_messages(self, messages):
        if any(MEMBER in m.to for m in messages):
            raise smtplib.SMTPRecipientsRefused({MEMBER: (550, b'5.1.1 user unknown')})
        return super().send_messages(messages)


class FailForCarlaBackend(LocmemBackend):
    def send_messages(self, messages):
        if any(CARLA in m.to for m in messages):
            raise smtplib.SMTPRecipientsRefused({CARLA: (550, b'5.1.1 user unknown')})
        return super().send_messages(messages)


def _to(address):
    return [m for m in mail.outbox if m.to == [address]]


class _Dunning(_Fixtures):
    def _base(self):
        self._setup()
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': CARLA})
        self.user.profile.display_name = 'Lucía Gómez'
        self.user.profile.save()

    def _active_sub_due_for_renewal(self):
        """A paid ACTIVE subscription whose period just ended (renewal due)."""
        sub = self.sub
        sub.status = SubscriptionStatus.ACTIVE
        sub.starts_at = timezone.now() - timedelta(days=31)
        sub.ends_at = timezone.now() - timedelta(hours=1)
        sub.save()
        return sub


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class FailedChargeEmailTests(_Dunning, APITestCase):
    """Part 1, through the real webhook endpoint."""

    def setUp(self):
        self._base()
        self.sub = self._active_sub_due_for_renewal()

    def _charge(self, mock_sdk, payment_id, status='rejected', ap_id=None):
        ap_id = ap_id or f'ap-{payment_id}'
        mock_sdk.return_value.invoice.return_value.get.return_value = FakeMPResponse(200, {
            'id': ap_id, 'preapproval_id': 'pre-1', 'type': 'recurring', 'status': 'processed',
            'transaction_amount': 55000, 'currency_id': 'ARS', 'external_reference': f'sub-{self.sub.id}',
            'payment': {'id': payment_id, 'status': status},
        })
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                f'{WEBHOOK_URL}?data.id={ap_id}&type=subscription_authorized_payment', data={}, format='json',
                headers={'x-request-id': 'r', 'x-signature': _signature_header(ap_id, 'r', '1700000000')},
            )
        self.sub.refresh_from_db()
        return resp

    @patch(SUB_SDK)
    def test_first_failure_emails_the_member_once(self, mock_sdk):
        resp = self._charge(mock_sdk, 'pay-f1')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertIsNotNone(self.sub.grace_ends_at)
        self.assertEqual(len(_to(MEMBER)), 1)
        email = _to(MEMBER)[0]
        self.assertEqual(email.subject, 'No pudimos cobrar tu membresía Plan Mensual')
        self.assertEqual(email.reply_to, [CARLA])
        grace_day = timezone.localtime(self.sub.grace_ends_at).strftime('%d/%m/%Y')
        for expected in (
            'Hola Lucía,', '55.000,00 ARS', f'seguís teniendo acceso a todos tus videos hasta el {grace_day}',
            'la tarjeta se maneja ahí, no en Recreo Bienestar', 'volver a intentar el cobro automáticamente',
            'https://recreobienestar.com/mi-cuenta/suscripcion/',
        ):
            self.assertIn(expected, email.body)
        for leaked in ('pay-f1', 'pre-1', 'token'):
            self.assertNotIn(leaked, email.body)
        self.assertEqual(_to(CARLA), [])  # Carla only hears about it if it lapses

    @patch(SUB_SDK)
    def test_mp_retries_during_the_same_grace_do_not_re_email(self, mock_sdk):
        self._charge(mock_sdk, 'pay-f1')
        grace = self.sub.grace_ends_at
        for retry in ('pay-f2', 'pay-f3', 'pay-f4'):
            self._charge(mock_sdk, retry)
        self._charge(mock_sdk, 'pay-f4')  # and a duplicate notification
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.grace_ends_at, grace)  # grace not pushed out either
        self.assertEqual(
            list(SubscriptionCharge.objects.order_by('id').values_list('outcome', flat=True)),
            [SubscriptionChargeOutcome.GRACE_STARTED] + [SubscriptionChargeOutcome.GRACE_RUNNING] * 3,
        )
        self.assertEqual(len(_to(MEMBER)), 1)

    @patch(SUB_SDK)
    def test_a_new_grace_period_after_a_successful_charge_emails_again(self, mock_sdk):
        self._charge(mock_sdk, 'pay-f1')
        self._charge(mock_sdk, 'pay-ok', status='approved')  # clears grace (renewal email too)
        self.assertIsNone(self.sub.grace_ends_at)
        self.sub.ends_at = timezone.now() - timedelta(hours=1)  # next period ended, renewal due again
        self.sub.save()
        self._charge(mock_sdk, 'pay-f9')
        grace_emails = [m for m in _to(MEMBER) if m.subject.startswith('No pudimos cobrar')]
        self.assertEqual(len(grace_emails), 2)

    @patch(SUB_SDK)
    def test_member_without_email_is_skipped_and_grace_still_starts(self, mock_sdk):
        self.user.email = ''
        self.user.save()
        with self.assertLogs('common.notifications', level='WARNING') as logs:
            self._charge(mock_sdk, 'pay-f1')
        self.assertIsNotNone(self.sub.grace_ends_at)
        self.assertEqual(mail.outbox, [])
        self.assertIn('has no email address', '\n'.join(logs.output))

    @override_settings(EMAIL_BACKEND=f'{_MODULE}.FailForMemberBackend')
    @patch(SUB_SDK)
    def test_email_failure_does_not_block_grace(self, mock_sdk):
        with self.assertLogs('common.notifications', level='ERROR'):
            resp = self._charge(mock_sdk, 'pay-f1')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(self.sub.grace_ends_at)
        self.assertEqual(SubscriptionCharge.objects.get().outcome, SubscriptionChargeOutcome.GRACE_STARTED)

    @patch(SUB_SDK)
    def test_failure_after_grace_ran_out_lapses_via_the_webhook_and_emails_once(self, mock_sdk):
        self._charge(mock_sdk, 'pay-f1')
        Subscription.objects.filter(pk=self.sub.pk).update(grace_ends_at=timezone.now() - timedelta(minutes=1))
        mail.outbox.clear()
        self._charge(mock_sdk, 'pay-f2')
        self.assertEqual(self.sub.status, SubscriptionStatus.PAST_DUE)
        self.assertEqual(len(_to(MEMBER)), 1)
        self.assertEqual(len(_to(CARLA)), 1)
        self._charge(mock_sdk, 'pay-f3')  # another failure on the PAST_DUE row
        self.assertEqual(len(mail.outbox), 2)


class LapseCommandTests(_Dunning, TestCase):
    """Parts 2 + 3: manage.py lapse_overdue_subscriptions."""

    def setUp(self):
        self._base()
        self.sub = self._active_sub_due_for_renewal()
        self.sub.grace_ends_at = timezone.now() - timedelta(hours=2)  # grace ran out, never paid
        self.sub.save()

    def _run(self, *args):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command('lapse_overdue_subscriptions', *args, stdout=out)
        self.sub.refresh_from_db()
        return out.getvalue()

    def test_elapsed_grace_flips_to_past_due_and_emails_member_and_carla(self):
        output = self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.PAST_DUE)
        member, carla = _to(MEMBER), _to(CARLA)
        self.assertEqual(len(member), 1)
        self.assertEqual(len(carla), 1)
        self.assertEqual(member[0].subject, 'Tu membresía Plan Mensual quedó suspendida')
        for expected in (
            'Hola Lucía,', 'quedó suspendida', 'ya no tenés acceso', 'tu acceso se reactiva solo',
            'https://recreobienestar.com/#columna-sana', 'https://recreobienestar.com/mi-cuenta/',
        ):
            self.assertIn(expected, member[0].body)
        self.assertEqual(carla[0].subject, 'Membresía suspendida por falta de pago: Plan Mensual — Lucía Gómez <socia@example.com>')
        for expected in ('Miembro: Lucía Gómez <socia@example.com>', 'Plan: Plan Mensual', '55.000,00 ARS',
                         f'suscripción #{self.sub.id}', 'Gracia hasta:'):
            self.assertIn(expected, carla[0].body)
        self.assertIn('set PAST_DUE, member + Carla notified', output)
        self.assertIn('1 candidate(s), 1 lapsed, 0 left alone, 0 older than 30 days untouched', output)

    def test_re_running_sends_nothing_more(self):
        self._run()
        output = self._run()
        self.assertIn('0 candidate(s)', output)
        self.assertEqual(len(mail.outbox), 2)

    def test_dry_run_changes_nothing(self):
        output = self._run('--dry-run')
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(mail.outbox, [])
        self.assertIn('would set PAST_DUE and notify member + Carla (dry run)', output)
        self.assertIn('lapse (dry run): 1 candidate(s), 1 would lapse', output)

    def test_grace_still_running_is_not_touched(self):
        self.sub.grace_ends_at = timezone.now() + timedelta(days=2)
        self.sub.save()
        self.assertIn('0 candidate(s)', self._run())
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)

    def test_stale_grace_on_a_still_paid_period_is_left_alone(self):
        self.sub.ends_at = timezone.now() + timedelta(days=10)
        self.sub.save()
        output = self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertIn('left alone', output)
        self.assertEqual(mail.outbox, [])

    def test_lapses_older_than_the_window_are_counted_but_untouched(self):
        self.sub.grace_ends_at = timezone.now() - timedelta(days=45)
        self.sub.save()
        output = self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertIn('0 candidate(s), 0 lapsed, 0 left alone, 1 older than 30 days untouched', output)
        self.assertEqual(mail.outbox, [])

    def test_member_without_email_is_skipped_but_carla_is_told(self):
        self.user.email = ''
        self.user.save()
        with self.assertLogs('common.notifications', level='WARNING'):
            self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.PAST_DUE)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(_to(CARLA)), 1)

    @override_settings(EMAIL_BACKEND=f'{_MODULE}.FailForMemberBackend')
    def test_member_email_failure_neither_blocks_the_change_nor_carlas_email(self):
        with self.assertLogs('common.notifications', level='ERROR'):
            self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.PAST_DUE)
        self.assertEqual(_to(MEMBER), [])
        self.assertEqual(len(_to(CARLA)), 1)

    @override_settings(EMAIL_BACKEND=f'{_MODULE}.FailForCarlaBackend')
    def test_carla_email_failure_does_not_stop_the_members(self):
        with self.assertLogs('common.notifications', level='ERROR'):
            self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.PAST_DUE)
        self.assertEqual(len(_to(MEMBER)), 1)
        self.assertEqual(_to(CARLA), [])

    @patch('common.notifications.transaction.on_commit', side_effect=RuntimeError('boom'))
    def test_failure_to_even_schedule_the_emails_does_not_block_the_change(self, _on_commit):
        with self.assertLogs('common.notifications', level='ERROR'):
            self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.PAST_DUE)


class LapseDoesNotChangeAccessTests(_Dunning, TestCase):
    """The command must be access-neutral: for every subscription shape it
    might meet, who can watch what is identical before and after it runs —
    and stays identical when a late MP charge (approved or failed) arrives."""

    def setUp(self):
        self._base()
        now = timezone.now()
        User = self.user.__class__
        self.cases = {}

        def make(name, **fields):
            user = User.objects.create_user(username=name, email=f'{name}@example.com', password='x')
            sub = Subscription.objects.create(
                user=user, plan=self.monthly, amount=Decimal('55000.00'), currency='ARS',
                starts_at=now - timedelta(days=60), **fields,
            )
            self.cases[name] = sub

        A, C, P, E = (SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED,
                      SubscriptionStatus.PAST_DUE, SubscriptionStatus.EXPIRED)
        make('lapsed', status=A, ends_at=now - timedelta(days=6), grace_ends_at=now - timedelta(hours=3))
        make('in_grace', status=A, ends_at=now - timedelta(days=1), grace_ends_at=now + timedelta(days=4))
        make('stale_grace', status=A, ends_at=now + timedelta(days=9), grace_ends_at=now - timedelta(days=3))
        make('paid_up', status=A, ends_at=now + timedelta(days=20))
        make('ended_no_grace', status=A, ends_at=now - timedelta(days=20))
        make('old_lapse', status=A, ends_at=now - timedelta(days=60), grace_ends_at=now - timedelta(days=50))
        make('cancelled_running', status=C, ends_at=now + timedelta(days=5))
        make('cancelled_ended', status=C, ends_at=now - timedelta(days=5))
        make('past_due', status=P, ends_at=now - timedelta(days=30), grace_ends_at=now - timedelta(days=25))
        make('expired', status=E, ends_at=now - timedelta(days=30))
        self.video = self.plan2_video

    def _access(self):
        return {name: can_access_video(sub.user, self.video) for name, sub in self.cases.items()}

    def test_no_one_gains_or_loses_access(self):
        before = self._access()
        with self.captureOnCommitCallbacks(execute=True):
            call_command('lapse_overdue_subscriptions', stdout=StringIO())
        self.assertEqual(self._access(), before)
        statuses = {n: Subscription.objects.get(pk=s.pk).status for n, s in self.cases.items()}
        self.assertEqual(statuses['lapsed'], SubscriptionStatus.PAST_DUE)  # the only change
        changed = [n for n, s in self.cases.items() if statuses[n] != s.status]
        self.assertEqual(changed, ['lapsed'])
        # Housekeeping that would have changed access is NOT done: an
        # ACTIVE subscription past its end with no grace stays ACTIVE.
        self.assertEqual(statuses['ended_no_grace'], SubscriptionStatus.ACTIVE)

    def test_late_mp_charges_behave_the_same_with_or_without_the_command(self):
        """The lapsed row as the command leaves it (PAST_DUE) vs as it was
        (ACTIVE, grace elapsed): a late failed or approved charge yields the
        same access either way."""
        lapsed = self.cases['lapsed']
        for apply in (_apply_failed_charge, _apply_approved_charge):
            with self.subTest(event=apply.__name__):
                as_was = Subscription.objects.get(pk=lapsed.pk)
                as_lapsed = Subscription.objects.get(pk=lapsed.pk)
                as_lapsed.status = SubscriptionStatus.PAST_DUE
                apply(as_was, timezone.now())
                apply(as_lapsed, timezone.now())
                self.assertEqual(as_was.is_active(), as_lapsed.is_active())
                self.assertEqual(as_was.status, as_lapsed.status)
