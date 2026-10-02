"""Early Mercado Pago notifications must not strand a paying member
(production incident, 02/10/2026: charge 7032483746 was notified before
MP's API could return it; the lookup 404'd, we answered 502, MP never
retried, the subscription stayed PENDING).

Fix 1: the authorized-payment lookup retries a 404 (only a 404) in-request.
Fix 2: reconcile_subscription_charges finds stranded subscriptions and
applies their approved charges through process_authorized_payment.

MP SDK always mocked; mail goes to locmem; nothing actually sleeps."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import call, patch

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from common.choices import SubscriptionStatus
from memberships.models import Subscription, SubscriptionCharge, SubscriptionChargeOutcome
from memberships.tests.test_subscription_webhooks import SUB_SDK, _Fixtures
from payments.tests.test_checkout import FakeMPResponse
from payments.tests.test_webhook import WEBHOOK_SECRET, WEBHOOK_URL, _signature_header
from site_content.models import SiteSettings

CARLA = 'carla@example.com'
SLEEP = 'memberships.webhooks._sleep'


def _authorized_payment(sub, ap_id='ap-1', payment_id='pay-1', payment_status='approved', amount=55000):
    return {
        'id': ap_id, 'preapproval_id': sub.mp_preapproval_id, 'type': 'recurring', 'status': 'processed',
        'transaction_amount': amount, 'currency_id': 'ARS', 'external_reference': f'sub-{sub.id}',
        'date_created': '2026-10-02T08:41:30.000-03:00',
        'payment': {'id': payment_id, 'status': payment_status},
    }


NOT_FOUND = FakeMPResponse(404, {'message': 'The Authorized Payment with id ap-1 does not exist'})


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class EarlyNotificationRetryTests(_Fixtures, APITestCase):
    """Fix 1, through the real webhook endpoint."""

    def setUp(self):
        self._setup()

    def _notify_charge(self, ap_id='ap-1'):
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                f'{WEBHOOK_URL}?data.id={ap_id}&type=subscription_authorized_payment', data={}, format='json',
                headers={'x-request-id': 'req-1', 'x-signature': _signature_header(ap_id, 'req-1', '1700000000')},
            )
        self.sub.refresh_from_db()
        return resp

    @override_settings(MP_AUTHORIZED_PAYMENT_LOOKUP_DELAYS=(1, 2, 4))
    @patch(SLEEP)
    @patch(SUB_SDK)
    def test_404_that_resolves_on_the_second_attempt_activates_normally(self, mock_sdk, mock_sleep):
        mock_sdk.return_value.invoice.return_value.get.side_effect = [
            NOT_FOUND, FakeMPResponse(200, _authorized_payment(self.sub)),
        ]
        resp = self._notify_charge()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(SubscriptionCharge.objects.get().outcome, SubscriptionChargeOutcome.ACTIVATED)
        self.assertEqual(mock_sdk.return_value.invoice.return_value.get.call_count, 2)
        mock_sleep.assert_called_once_with(1)
        self.assertEqual(len([m for m in mail.outbox if m.to == [self.user.email]]), 1)

    @override_settings(MP_AUTHORIZED_PAYMENT_LOOKUP_DELAYS=(1, 2, 4))
    @patch(SLEEP)
    @patch(SUB_SDK)
    def test_404_that_never_resolves_still_returns_502_after_the_retries(self, mock_sdk, mock_sleep):
        mock_sdk.return_value.invoice.return_value.get.return_value = NOT_FOUND
        resp = self._notify_charge()
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(mock_sdk.return_value.invoice.return_value.get.call_count, 4)
        self.assertEqual(mock_sleep.call_args_list, [call(1), call(2), call(4)])
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertFalse(SubscriptionCharge.objects.exists())
        self.assertEqual(mail.outbox, [])

    @override_settings(MP_AUTHORIZED_PAYMENT_LOOKUP_DELAYS=(1, 2, 4))
    @patch(SLEEP)
    @patch(SUB_SDK)
    def test_non_404_errors_are_not_retried(self, mock_sdk, mock_sleep):
        for status_code in (401, 403, 500):
            with self.subTest(status_code=status_code):
                get = mock_sdk.return_value.invoice.return_value.get
                get.reset_mock()
                get.return_value = FakeMPResponse(status_code, {'message': 'nope'})
                self.assertEqual(self._notify_charge().status_code, 502)
                self.assertEqual(get.call_count, 1)
        mock_sleep.assert_not_called()

    @override_settings(MP_AUTHORIZED_PAYMENT_LOOKUP_DELAYS=(1, 2, 4))
    @patch(SLEEP)
    @patch(SUB_SDK)
    def test_a_network_error_is_not_retried_either(self, mock_sdk, mock_sleep):
        mock_sdk.return_value.invoice.return_value.get.side_effect = ConnectionError('reset')
        self.assertEqual(self._notify_charge().status_code, 502)
        self.assertEqual(mock_sdk.return_value.invoice.return_value.get.call_count, 1)
        mock_sleep.assert_not_called()

    @patch(SLEEP)
    @patch(SUB_SDK)
    def test_the_test_suite_retries_without_sleeping(self, mock_sdk, mock_sleep):
        # settings_test_sqlite sets the delays to zeros: retried, never slept.
        mock_sdk.return_value.invoice.return_value.get.side_effect = [
            NOT_FOUND, NOT_FOUND, FakeMPResponse(200, _authorized_payment(self.sub)),
        ]
        self.assertEqual(self._notify_charge().status_code, 200)
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        mock_sleep.assert_not_called()


class ReconcileCommandTests(_Fixtures, TestCase):
    """Fix 2: manage.py reconcile_subscription_charges."""

    def setUp(self):
        self._setup()
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': CARLA})
        self.sub.mp_status = 'authorized'
        self.sub.save()

    def _mp(self, mock_sdk, search_results, by_id=None):
        invoice = mock_sdk.return_value.invoice.return_value
        invoice.search.return_value = FakeMPResponse(200, {'results': search_results, 'paging': {}})
        by_id = by_id or {item['id']: item for item in search_results}
        invoice.get.side_effect = lambda ap_id: FakeMPResponse(200, by_id[ap_id])
        return invoice

    def _run(self, *args):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command('reconcile_subscription_charges', *args, stdout=out)
        self.sub.refresh_from_db()
        return out.getvalue()

    def _emails(self):
        return {
            'carla': [m for m in mail.outbox if m.to == [CARLA]],
            'buyer': [m for m in mail.outbox if m.to == [self.user.email]],
        }

    @patch(SUB_SDK)
    def test_stranded_subscription_is_activated_and_both_emails_go_out(self, mock_sdk):
        invoice = self._mp(mock_sdk, [_authorized_payment(self.sub)])
        output = self._run()
        invoice.search.assert_called_once_with(filters={'preapproval_id': 'pre-1'})
        invoice.get.assert_called_once_with('ap-1')  # re-fetched by process_authorized_payment
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertIsNotNone(self.sub.ends_at)
        self.assertEqual(SubscriptionCharge.objects.get().outcome, SubscriptionChargeOutcome.ACTIVATED)
        emails = self._emails()
        self.assertEqual(len(emails['carla']), 1)
        self.assertEqual(len(emails['buyer']), 1)
        self.assertEqual(emails['buyer'][0].subject, '¡Te damos la bienvenida a Plan Mensual!')
        self.assertIn(f'sub #{self.sub.id}', output)
        self.assertIn('-> applied (http=200, status=active)', output)
        self.assertIn('1 candidate(s), 1 activated, 0 without an approved charge, 0 errors', output)

    @patch(SUB_SDK)
    def test_running_it_twice_sends_no_second_email(self, mock_sdk):
        invoice = self._mp(mock_sdk, [_authorized_payment(self.sub)])
        self._run()
        output = self._run()
        self.assertIn('0 candidate(s)', output)
        self.assertEqual(invoice.search.call_count, 1)  # activated sub isn't selected again
        self.assertEqual(SubscriptionCharge.objects.count(), 1)
        self.assertEqual(len(self._emails()['carla']), 1)
        self.assertEqual(len(self._emails()['buyer']), 1)

    @patch(SUB_SDK)
    def test_the_duplicate_guard_holds_even_if_a_charge_were_offered_again(self, mock_sdk):
        # The charge was already applied by the webhook, but the sub is
        # (artificially) back to PENDING with no ACTIVATED charge: the
        # command selects it, and process_authorized_payment's own guard
        # (payment id already approved) makes it a no-op — no email.
        self._mp(mock_sdk, [_authorized_payment(self.sub)])
        SubscriptionCharge.objects.create(
            subscription=self.sub, mp_authorized_payment_id='ap-1', mp_payment_id='pay-1',
            mp_payment_status='approved', outcome=SubscriptionChargeOutcome.AMOUNT_MISMATCH,
        )
        self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(mail.outbox, [])

    @patch(SUB_SDK)
    def test_already_active_subscription_is_untouched(self, mock_sdk):
        self._make_active(self.sub)
        before = Subscription.objects.get(pk=self.sub.pk).updated_at
        output = self._run()
        mock_sdk.return_value.invoice.return_value.search.assert_not_called()
        self.assertEqual(Subscription.objects.get(pk=self.sub.pk).updated_at, before)
        self.assertIn('0 candidate(s)', output)
        self.assertEqual(mail.outbox, [])

    @patch(SUB_SDK)
    def test_no_approved_charge_at_mp_leaves_it_alone(self, mock_sdk):
        for results in ([], [_authorized_payment(self.sub, payment_status='rejected')],
                        [_authorized_payment(self.sub, payment_status='in_process')]):
            with self.subTest(results=[r['payment']['status'] for r in results]):
                invoice = self._mp(mock_sdk, results)
                output = self._run()
                invoice.get.assert_not_called()
                self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
                self.assertIn('no approved charge at MP — left pending', output)
        self.assertFalse(SubscriptionCharge.objects.exists())
        self.assertEqual(mail.outbox, [])

    @patch(SUB_SDK)
    def test_amount_mismatch_reported_by_mp_still_does_not_grant_access(self, mock_sdk):
        # The search says approved, but the re-fetched charge is for the
        # wrong amount: process_authorized_payment refuses, as it always has.
        self._mp(mock_sdk, [_authorized_payment(self.sub, amount=1)])
        self._run()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(SubscriptionCharge.objects.get().outcome, SubscriptionChargeOutcome.AMOUNT_MISMATCH)
        self.assertEqual(mail.outbox, [])

    @patch(SUB_SDK)
    def test_dry_run_changes_nothing(self, mock_sdk):
        invoice = self._mp(mock_sdk, [_authorized_payment(self.sub)])
        output = self._run('--dry-run')
        invoice.get.assert_not_called()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertFalse(SubscriptionCharge.objects.exists())
        self.assertEqual(mail.outbox, [])
        self.assertIn('approved charge ap-1 -> would apply (dry run)', output)
        self.assertIn('reconcile (dry run): 1 candidate(s), 1 would activate', output)

    @patch(SUB_SDK)
    def test_only_recent_pending_paid_subscriptions_with_a_preapproval_are_considered(self, mock_sdk):
        Subscription.objects.filter(pk=self.sub.pk).update(created_at=timezone.now() - timedelta(days=8))
        self._trial(user=self.user)
        no_preapproval = self._pending(self.yearly, preapproval_id='')
        output = self._run()
        mock_sdk.return_value.invoice.return_value.search.assert_not_called()
        self.assertIn('0 candidate(s)', output)
        no_preapproval.refresh_from_db()
        self.assertEqual(no_preapproval.status, SubscriptionStatus.PENDING)
        # ...but a wider window picks the older one up.
        self._mp(mock_sdk, [_authorized_payment(self.sub)])
        self._run('--days', '30')
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)

    @patch(SUB_SDK)
    def test_mp_search_failure_is_reported_and_others_still_run(self, mock_sdk):
        other_user = self.user.__class__.objects.create_user(username='otra', email='otra@example.com', password='x')
        other = self._pending(self.monthly, user=other_user, preapproval_id='pre-2')
        ok_payment = _authorized_payment(other, ap_id='ap-2', payment_id='pay-2')
        invoice = mock_sdk.return_value.invoice.return_value
        invoice.search.side_effect = [
            FakeMPResponse(500, {'message': 'boom'}),
            FakeMPResponse(200, {'results': [ok_payment]}),
        ]
        invoice.get.side_effect = lambda ap_id: FakeMPResponse(200, ok_payment)
        output = self._run()
        other.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(other.status, SubscriptionStatus.ACTIVE)
        self.assertIn('MP search failed', output)
        self.assertIn('2 candidate(s), 1 activated, 0 without an approved charge, 1 errors', output)


class ReconcileDoesNotTouchPaymentsTests(TestCase):
    def test_reconcile_uses_the_webhook_function_not_a_copy(self):
        from memberships.management.commands import reconcile_subscription_charges as command
        from memberships import webhooks

        self.assertIs(command.process_authorized_payment, webhooks.process_authorized_payment)

    def test_default_window_is_seven_days(self):
        from memberships.management.commands.reconcile_subscription_charges import DEFAULT_WINDOW_DAYS

        self.assertEqual(DEFAULT_WINDOW_DAYS, 7)


class RetryScheduleTests(TestCase):
    def test_production_schedule_is_1_2_4_seconds(self):
        # Production doesn't set MP_AUTHORIZED_PAYMENT_LOOKUP_DELAYS, so the
        # module default applies there. (Read without mutating settings —
        # deleting a setting inside a class-level override leaks into later
        # tests, which then really sleep.)
        import importlib

        from memberships import webhooks

        production_settings = importlib.import_module('config.settings')
        self.assertFalse(hasattr(production_settings, 'MP_AUTHORIZED_PAYMENT_LOOKUP_DELAYS'))
        self.assertEqual(webhooks.AUTHORIZED_PAYMENT_LOOKUP_DELAYS, (1, 2, 4))
        self.assertEqual(sum(webhooks.AUTHORIZED_PAYMENT_LOOKUP_DELAYS), 7)
