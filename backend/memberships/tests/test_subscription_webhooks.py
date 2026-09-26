"""Phase 5B-2b: Mercado Pago SUBSCRIPTION notifications through the real
webhook endpoint (signature and all), plus member cancellation. The MP
SDK is ALWAYS mocked — no real network call."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from catalog.models import Category, Video
from common.choices import SubscriptionStatus
from memberships.models import (
    MembershipPlan, Subscription, SubscriptionCharge, SubscriptionChargeOutcome,
)
from memberships.services import _add_months, can_access_video, user_has_active_trial
from payments.models import OfferingPurchase, PurchaseStatus
from payments.tests.test_checkout import FakeMPResponse
from payments.tests.test_webhook import WEBHOOK_SECRET, WEBHOOK_URL, _signature_header
from site_content.models import Offering

User = get_user_model()

SUB_SDK = 'memberships.webhooks.mercadopago.SDK'
PAYMENTS_SDK = 'payments.views.mercadopago.SDK'
CANCEL_SDK = 'memberships.views.mercadopago.SDK'


class _Fixtures:
    def _setup(self):
        self.category = Category.objects.create(name='Pilates')
        self.user = User.objects.create_user(username='socia', email='socia@example.com', password='x')
        self.trial_plan = MembershipPlan.objects.create(
            tier='plan1', name='FREE TRIAL', price=0, duration_days=7, is_active=True,
        )
        self.monthly = MembershipPlan.objects.create(
            tier='plan2', name='Plan Mensual', price=Decimal('55000.00'), currency='ARS',
            duration_days=30, grace_days=5, is_active=True,
        )
        self.yearly = MembershipPlan.objects.create(
            tier='plan3', name='Plan Anual', price=Decimal('300000.00'), currency='ARS',
            duration_days=365, grace_days=5, is_active=True,
        )
        self.plan2_video = Video.objects.create(
            title='Plan 2', youtube_url='https://youtu.be/dQw4w9WgXcQ', category=self.category,
            is_published=True, access_level='plan2',
        )
        self.sub = self._pending(self.monthly)

    def _pending(self, plan, user=None, preapproval_id='pre-1'):
        return Subscription.objects.create(
            user=user or self.user, plan=plan, status=SubscriptionStatus.PENDING,
            amount=plan.price, currency=plan.currency, mp_preapproval_id=preapproval_id,
            mp_status='pending',
        )

    def _trial(self, user=None):
        now = timezone.now()
        return Subscription.objects.create(
            user=user or self.user, plan=self.trial_plan, status=SubscriptionStatus.TRIAL,
            starts_at=now, ends_at=now + timedelta(days=7), trial_ends_at=now + timedelta(days=7),
            is_trial=True,
        )

    def _make_active(self, sub, ends_at=None):
        sub.status = SubscriptionStatus.ACTIVE
        sub.ends_at = ends_at or timezone.now() + timedelta(days=20)
        sub.save()
        return sub


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class SubscriptionWebhookTests(_Fixtures, APITestCase):
    def setUp(self):
        self._setup()

    def _notify(self, notification_type, data_id, valid_signature=True):
        headers = {'x-request-id': 'req-1'}
        if valid_signature:
            headers['x-signature'] = _signature_header(data_id, 'req-1', '1700000000')
        else:
            headers['x-signature'] = _signature_header(data_id, 'req-1', '1700000000', secret='nope')
        return self.client.post(
            f'{WEBHOOK_URL}?data.id={data_id}&type={notification_type}', data={},
            format='json', headers=headers,
        )

    def _charge(self, mock_sdk, payment_status='approved', payment_id='pay-1', amount=55000,
                currency='ARS', preapproval_id='pre-1', external_reference=None, ap_id='ap-1'):
        ref = f'sub-{self.sub.id}' if external_reference is None else external_reference
        mock_sdk.return_value.invoice.return_value.get.return_value = FakeMPResponse(200, {
            'id': ap_id, 'preapproval_id': preapproval_id, 'type': 'recurring', 'status': 'processed',
            'transaction_amount': amount, 'currency_id': currency, 'external_reference': ref,
            'payment': {'id': payment_id, 'status': payment_status},
        })
        return self._notify('subscription_authorized_payment', ap_id)

    def _preapproval(self, mock_sdk, mp_status, preapproval_id='pre-1', external_reference=None):
        ref = f'sub-{self.sub.id}' if external_reference is None else external_reference
        mock_sdk.return_value.preapproval.return_value.get.return_value = FakeMPResponse(200, {
            'id': preapproval_id, 'status': mp_status, 'external_reference': ref,
            'next_payment_date': '2026-10-26T17:29:48.000-04:00', 'payer_id': 263055090,
        })
        return self._notify('subscription_preapproval', preapproval_id)

    # ── the existing type=payment path (regression) ────────────────────
    @patch(PAYMENTS_SDK)
    def test_type_payment_with_offering_reference_still_completes_purchase(self, mock_sdk):
        buyer = User.objects.create_user(username='compradora', password='x')
        offering = Offering.objects.create(name='Curso', price=Decimal('1000.00'), currency='ARS', is_active=True)
        purchase = OfferingPurchase.objects.create(
            user=buyer, offering=offering, status=PurchaseStatus.PENDING,
            amount=Decimal('1000.00'), currency='ARS',
        )
        mock_sdk.return_value.payment.return_value.get.return_value = FakeMPResponse(200, {
            'id': 'mp-pay-9', 'status': 'approved', 'transaction_amount': '1000.00',
            'currency_id': 'ARS', 'external_reference': str(purchase.id),
        })
        resp = self._notify('payment', 'mp-pay-9')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, PurchaseStatus.COMPLETED)

    @patch(PAYMENTS_SDK)
    def test_type_payment_with_sub_reference_leaves_colliding_purchase_untouched(self, mock_sdk):
        buyer = User.objects.create_user(username='compradora', password='x')
        offering = Offering.objects.create(name='Curso', price=Decimal('55000.00'), currency='ARS', is_active=True)
        purchase = OfferingPurchase.objects.create(
            user=buyer, offering=offering, status=PurchaseStatus.PENDING,
            amount=Decimal('55000.00'), currency='ARS',
        )
        mock_sdk.return_value.payment.return_value.get.return_value = FakeMPResponse(200, {
            'id': 'mp-pay-10', 'status': 'approved', 'transaction_amount': 55000,
            'currency_id': 'ARS', 'external_reference': f'sub-{purchase.id}',
        })
        resp = self._notify('payment', 'mp-pay-10')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, PurchaseStatus.PENDING)
        self.assertIsNone(purchase.mp_payment_id)

    # ── signature still gates the new branch ───────────────────────────
    @patch(SUB_SDK)
    def test_subscription_notification_with_bad_signature_rejected(self, mock_sdk):
        resp = self._notify('subscription_authorized_payment', 'ap-1', valid_signature=False)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        mock_sdk.assert_not_called()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)

    @patch(SUB_SDK)
    @patch(PAYMENTS_SDK)
    def test_unknown_type_is_acknowledged_and_ignored(self, payments_sdk, sub_sdk):
        resp = self._notify('order', '123456')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        payments_sdk.assert_not_called()
        sub_sdk.assert_not_called()

    # ── approved charge ────────────────────────────────────────────────
    @patch(SUB_SDK)
    def test_approved_charge_activates_supersedes_trial_and_clears_grace(self, mock_sdk):
        trial = self._trial()
        self.sub.grace_ends_at = timezone.now() + timedelta(days=2)
        self.sub.save()

        before = timezone.now()
        resp = self._charge(mock_sdk)
        after = timezone.now()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_sdk.return_value.invoice.return_value.get.assert_called_once_with('ap-1')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertTrue(before <= self.sub.starts_at <= after)
        self.assertTrue(_add_months(before, 1) <= self.sub.ends_at <= _add_months(after, 1))
        self.assertIsNone(self.sub.grace_ends_at)
        self.assertEqual((self.sub.last_charge_payment_id, self.sub.last_charge_status), ('pay-1', 'approved'))
        trial.refresh_from_db()
        self.assertEqual(trial.superseded_by, self.sub)
        self.assertFalse(user_has_active_trial(self.user))
        charge = SubscriptionCharge.objects.get()
        self.assertEqual(charge.outcome, SubscriptionChargeOutcome.ACTIVATED)
        self.assertEqual(charge.mp_authorized_payment_id, 'ap-1')
        # End to end: the confirmed charge is what grants access.
        self.assertTrue(can_access_video(self.user, self.plan2_video))

    @patch(SUB_SDK)
    def test_approved_charge_on_yearly_plan_adds_twelve_months(self, mock_sdk):
        self.sub = self._pending(self.yearly, preapproval_id='pre-y')
        before = timezone.now()
        self._charge(mock_sdk, amount=300000, preapproval_id='pre-y')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertTrue(_add_months(before, 12) <= self.sub.ends_at <= _add_months(timezone.now(), 12))

    @patch(SUB_SDK)
    def test_same_charge_delivered_twice_is_applied_once(self, mock_sdk):
        self.assertEqual(self._charge(mock_sdk).status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        first_ends_at = self.sub.ends_at

        self.assertEqual(self._charge(mock_sdk).status_code, status.HTTP_200_OK)
        self.assertEqual(self._charge(mock_sdk).status_code, status.HTTP_200_OK)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.ends_at, first_ends_at)
        self.assertEqual(SubscriptionCharge.objects.count(), 1)

    @patch(SUB_SDK)
    def test_next_months_charge_extends_again(self, mock_sdk):
        self._charge(mock_sdk, payment_id='pay-1', ap_id='ap-1')
        self.sub.refresh_from_db()
        Subscription.objects.filter(pk=self.sub.pk).update(ends_at=timezone.now())

        self._charge(mock_sdk, payment_id='pay-2', ap_id='ap-2')

        self.sub.refresh_from_db()
        self.assertGreater(self.sub.ends_at, timezone.now() + timedelta(days=27))
        self.assertEqual(SubscriptionCharge.objects.count(), 2)

    @patch(SUB_SDK)
    def test_legacy_bare_reference_still_maps(self, mock_sdk):
        self._charge(mock_sdk, external_reference=str(self.sub.id))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)

    # ── failed charges: grace, then PAST_DUE ───────────────────────────
    @patch(SUB_SDK)
    def test_rejected_charge_starts_grace_and_keeps_access(self, mock_sdk):
        self._make_active(self.sub, ends_at=timezone.now() - timedelta(minutes=1))
        self.assertFalse(can_access_video(self.user, self.plan2_video))

        resp = self._charge(mock_sdk, payment_status='rejected')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertAlmostEqual(
            self.sub.grace_ends_at, timezone.now() + timedelta(days=5), delta=timedelta(seconds=10),
        )
        self.assertTrue(can_access_video(self.user, self.plan2_video))
        self.assertEqual(SubscriptionCharge.objects.get().outcome, SubscriptionChargeOutcome.GRACE_STARTED)

    @patch(SUB_SDK)
    def test_retry_failing_again_does_not_extend_grace(self, mock_sdk):
        self._make_active(self.sub, ends_at=timezone.now() - timedelta(minutes=1))
        self._charge(mock_sdk, payment_status='rejected', payment_id='pay-1')
        self.sub.refresh_from_db()
        grace = self.sub.grace_ends_at

        self._charge(mock_sdk, payment_status='rejected', payment_id='pay-2')

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.grace_ends_at, grace)
        self.assertEqual(SubscriptionCharge.objects.get(mp_payment_id='pay-2').outcome,
                         SubscriptionChargeOutcome.GRACE_RUNNING)

    @patch(SUB_SDK)
    def test_rejected_charge_after_grace_elapsed_goes_past_due(self, mock_sdk):
        self._make_active(self.sub, ends_at=timezone.now() - timedelta(days=10))
        self.sub.grace_ends_at = timezone.now() - timedelta(days=1)
        self.sub.save()

        self._charge(mock_sdk, payment_status='rejected', payment_id='pay-9')

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PAST_DUE)
        self.assertFalse(can_access_video(self.user, self.plan2_video))

    @patch(SUB_SDK)
    def test_retry_succeeding_during_grace_reactivates_and_clears_grace(self, mock_sdk):
        self._make_active(self.sub, ends_at=timezone.now() - timedelta(minutes=1))
        self._charge(mock_sdk, payment_status='rejected', payment_id='pay-1')
        self._charge(mock_sdk, payment_status='approved', payment_id='pay-2')
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.grace_ends_at)
        self.assertGreater(self.sub.ends_at, timezone.now() + timedelta(days=27))

    @patch(SUB_SDK)
    def test_stale_rejection_after_approval_does_not_revoke(self, mock_sdk):
        self._charge(mock_sdk, payment_status='approved', payment_id='pay-2')
        self._charge(mock_sdk, payment_status='rejected', payment_id='pay-1')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertTrue(can_access_video(self.user, self.plan2_video))

    @patch(SUB_SDK)
    def test_first_charge_rejected_stays_pending_without_access(self, mock_sdk):
        self._charge(mock_sdk, payment_status='rejected')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertIsNone(self.sub.grace_ends_at)
        self.assertFalse(can_access_video(self.user, self.plan2_video))

    # ── anomalies ──────────────────────────────────────────────────────
    @patch(SUB_SDK)
    def test_amount_mismatch_not_activated_and_flagged(self, mock_sdk):
        resp = self._charge(mock_sdk, amount=1)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertIsNone(self.sub.ends_at)
        self.assertEqual(SubscriptionCharge.objects.get().outcome, SubscriptionChargeOutcome.AMOUNT_MISMATCH)
        self.assertFalse(can_access_video(self.user, self.plan2_video))

    @patch(SUB_SDK)
    def test_currency_mismatch_not_activated(self, mock_sdk):
        self._charge(mock_sdk, currency='USD')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)

    @patch(SUB_SDK)
    def test_unknown_preapproval_changes_nothing(self, mock_sdk):
        resp = self._charge(mock_sdk, preapproval_id='pre-unknown')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertFalse(SubscriptionCharge.objects.exists())
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)

        resp = self._preapproval(mock_sdk, 'cancelled', preapproval_id='pre-unknown')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Subscription.objects.count(), 1)

    @patch(SUB_SDK)
    def test_contradicting_external_reference_changes_nothing(self, mock_sdk):
        self._charge(mock_sdk, external_reference='sub-999')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertFalse(SubscriptionCharge.objects.exists())

    @patch(SUB_SDK)
    def test_mp_lookup_failure_returns_502_for_retry(self, mock_sdk):
        mock_sdk.return_value.invoice.return_value.get.return_value = FakeMPResponse(404, {'message': 'nf'})
        self.assertEqual(self._notify('subscription_authorized_payment', 'ap-x').status_code, 502)
        mock_sdk.return_value.preapproval.return_value.get.side_effect = ConnectionError()
        self.assertEqual(self._notify('subscription_preapproval', 'pre-1').status_code, 502)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)

    # ── subscription_preapproval ───────────────────────────────────────
    @patch(SUB_SDK)
    def test_preapproval_authorized_records_state_but_grants_nothing(self, mock_sdk):
        resp = self._preapproval(mock_sdk, 'authorized')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(self.sub.mp_status, 'authorized')
        self.assertEqual(self.sub.mp_payer_id, '263055090')
        self.assertIsNotNone(self.sub.next_payment_date)
        self.assertFalse(can_access_video(self.user, self.plan2_video))

    @patch(SUB_SDK)
    def test_preapproval_cancelled_keeps_access_until_ends_at(self, mock_sdk):
        ends_at = timezone.now() + timedelta(days=12)
        self._make_active(self.sub, ends_at=ends_at)

        self._preapproval(mock_sdk, 'cancelled')

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.CANCELLED)
        self.assertEqual(self.sub.ends_at, ends_at)
        self.assertIsNotNone(self.sub.cancelled_at)
        self.assertTrue(can_access_video(self.user, self.plan2_video))

        first_cancelled_at = self.sub.cancelled_at
        self._preapproval(mock_sdk, 'cancelled')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.cancelled_at, first_cancelled_at)
        self.assertEqual(self.sub.ends_at, ends_at)

    @patch(SUB_SDK)
    def test_preapproval_cancelled_before_any_charge_never_grants_access(self, mock_sdk):
        self._preapproval(mock_sdk, 'cancelled')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.EXPIRED)
        self.assertFalse(self.sub.is_active())
        self.assertFalse(can_access_video(self.user, self.plan2_video))


class CancelSubscriptionTests(_Fixtures, APITestCase):
    def setUp(self):
        self._setup()
        self.ends_at = timezone.now() + timedelta(days=12)
        self._make_active(self.sub, ends_at=self.ends_at)

    def _cancel(self, sub_id=None):
        return self.client.post(
            '/api/subscription/cancel/', {'subscription': sub_id or self.sub.id}, format='json',
        )

    @patch(CANCEL_SDK)
    def test_member_cancels_own_subscription_and_keeps_access(self, mock_sdk):
        update = mock_sdk.return_value.preapproval.return_value.update
        update.return_value = FakeMPResponse(200, {'id': 'pre-1', 'status': 'cancelled'})
        self.client.force_login(self.user)

        resp = self._cancel()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        update.assert_called_once_with('pre-1', {'status': 'cancelled'})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.CANCELLED)
        self.assertEqual(self.sub.ends_at, self.ends_at)
        self.assertIsNotNone(self.sub.cancelled_at)
        self.assertEqual(resp.data['access_until'], self.ends_at)
        self.assertTrue(can_access_video(self.user, self.plan2_video))

    @patch(CANCEL_SDK)
    def test_cannot_cancel_someone_elses_subscription(self, mock_sdk):
        other = User.objects.create_user(username='otra', email='o@example.com', password='x')
        self.client.force_login(other)

        resp = self._cancel()

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        mock_sdk.assert_not_called()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertIsNone(self.sub.cancelled_at)

    @patch(CANCEL_SDK)
    def test_mp_failure_leaves_subscription_uncancelled(self, mock_sdk):
        mock_sdk.return_value.preapproval.return_value.update.return_value = FakeMPResponse(500, {'message': 'x'})
        self.client.force_login(self.user)

        resp = self._cancel()

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn('No se cambió nada', resp.data['detail'])
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertIsNone(self.sub.cancelled_at)

    @patch(CANCEL_SDK)
    def test_double_cancel_is_a_noop(self, mock_sdk):
        update = mock_sdk.return_value.preapproval.return_value.update
        update.return_value = FakeMPResponse(200, {'status': 'cancelled'})
        self.client.force_login(self.user)
        self._cancel()
        self.sub.refresh_from_db()
        first = self.sub.cancelled_at

        resp = self._cancel()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(update.call_count, 1)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.cancelled_at, first)
        self.assertEqual(self.sub.ends_at, self.ends_at)

    @patch(CANCEL_SDK)
    def test_cancelling_a_never_paid_pending_subscription_expires_it(self, mock_sdk):
        mock_sdk.return_value.preapproval.return_value.update.return_value = FakeMPResponse(200, {})
        pending = self._pending(self.yearly, preapproval_id='pre-2')
        self.client.force_login(self.user)

        self.assertEqual(self._cancel(pending.id).status_code, status.HTTP_200_OK)

        pending.refresh_from_db()
        self.assertEqual(pending.status, SubscriptionStatus.EXPIRED)
        self.assertFalse(pending.is_active())

    @patch(CANCEL_SDK)
    def test_anonymous_and_missing_csrf_rejected(self, mock_sdk):
        self.assertIn(self._cancel().status_code, (401, 403))
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        resp = client.post('/api/subscription/cancel/', {'subscription': self.sub.id}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        mock_sdk.assert_not_called()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)


class MiSuscripcionPageTests(_Fixtures, TestCase):
    def setUp(self):
        self._setup()

    def test_anonymous_redirected(self):
        resp = self.client.get('/mi-cuenta/suscripcion/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/ingresar/', resp['Location'])

    def test_active_shows_cancel_button(self):
        self._make_active(self.sub)
        self.client.force_login(self.user)
        resp = self.client.get('/mi-cuenta/suscripcion/')
        self.assertContains(resp, 'data-cancel-form')
        self.assertContains(resp, f'data-subscription-id="{self.sub.id}"')

    def test_cancelled_shows_access_until_and_no_button(self):
        ends_at = timezone.now() + timedelta(days=12)
        self._make_active(self.sub, ends_at=ends_at)
        self.sub.status = SubscriptionStatus.CANCELLED
        self.sub.cancelled_at = timezone.now()
        self.sub.save()
        self.client.force_login(self.user)
        resp = self.client.get('/mi-cuenta/suscripcion/')
        self.assertNotContains(resp, 'data-cancel-form')
        self.assertContains(resp, 'Seguís teniendo acceso hasta el')
        self.assertContains(resp, timezone.localtime(ends_at).strftime('%d/%m/%Y'))

    def test_other_users_subscription_not_shown(self):
        self._make_active(self.sub)
        other = User.objects.create_user(username='otra', email='o@example.com', password='x')
        self.client.force_login(other)
        resp = self.client.get('/mi-cuenta/suscripcion/')
        self.assertNotContains(resp, 'data-cancel-form')
        self.assertContains(resp, 'No tenés una suscripción paga')
