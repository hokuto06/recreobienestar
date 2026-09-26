"""Phase 5B-2a: paid-plan signup — POST /api/subscribe/
(StartSubscriptionView) and GET /membresia/<slug>/, /membresia/estado/.
The Mercado Pago SDK is ALWAYS mocked here — no real network call to MP,
sandbox or otherwise."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import Category, Video
from common.choices import ENTITLED_STATUSES, SubscriptionStatus
from memberships.models import MembershipPlan, Subscription
from memberships.services import (
    billing_cadence_for_plan, can_access_video, supersede_active_trial, user_has_active_trial,
)
from memberships.views import PENDING_SIGNUP_REUSE_WINDOW
from payments.tests.test_checkout import FakeMPResponse

User = get_user_model()

SDK_PATH = 'memberships.views.mercadopago.SDK'


def _ok_preapproval(**overrides):
    response = {
        'id': 'pre-123', 'status': 'pending',
        'init_point': 'https://www.mercadopago.com.ar/subscriptions/checkout?preapproval_id=pre-123',
    }
    response.update(overrides)
    return FakeMPResponse(201, response)


class _PlansMixin:
    def _make_plans(self):
        self.trial_plan = MembershipPlan.objects.create(
            tier='plan1', name='FREE TRIAL', price=0, duration_days=7, trial_days=7, is_active=True,
        )
        self.monthly = MembershipPlan.objects.create(
            tier='plan2', name='Plan Mensual', price=Decimal('55000.00'), currency='ARS',
            duration_days=30, is_active=True,
        )
        self.yearly = MembershipPlan.objects.create(
            tier='plan3', name='Plan Anual', price=Decimal('300000.00'), currency='ARS',
            duration_days=365, is_active=True,
        )


class BillingCadenceTests(_PlansMixin, TestCase):
    def setUp(self):
        self._make_plans()

    def test_monthly_and_yearly_cadence(self):
        self.assertEqual(billing_cadence_for_plan(self.monthly), (1, 'months'))
        self.assertEqual(billing_cadence_for_plan(self.yearly), (12, 'months'))

    def test_unknown_duration_has_no_cadence(self):
        self.monthly.duration_days = None
        self.assertIsNone(billing_cadence_for_plan(self.monthly))
        self.monthly.duration_days = 31
        self.assertIsNone(billing_cadence_for_plan(self.monthly))


class StartSubscriptionViewTests(_PlansMixin, APITestCase):
    def setUp(self):
        self._make_plans()
        self.user = User.objects.create_user(
            username='socia', email='socia@example.com', password='x',
        )
        self.category = Category.objects.create(name='Pilates')

    def _post(self, data):
        return self.client.post('/api/subscribe/', data, format='json')

    def _start_trial(self, user=None):
        now = timezone.now()
        return Subscription.objects.create(
            user=user or self.user, plan=self.trial_plan, status=SubscriptionStatus.TRIAL,
            starts_at=now, ends_at=now + timedelta(days=7), trial_ends_at=now + timedelta(days=7),
            is_trial=True,
        )

    # ── happy path ─────────────────────────────────────────────────────
    @patch(SDK_PATH)
    def test_signup_creates_pending_non_entitled_subscription(self, mock_sdk_class):
        mock_sdk_class.return_value.preapproval.return_value.create.return_value = _ok_preapproval()
        self.client.force_login(self.user)

        resp = self._post({'plan': self.monthly.slug})

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['init_point'], _ok_preapproval()['response']['init_point'])
        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(sub.plan, self.monthly)
        self.assertEqual(sub.status, SubscriptionStatus.PENDING)
        self.assertNotIn(SubscriptionStatus.PENDING, ENTITLED_STATUSES)
        self.assertFalse(sub.is_active())
        self.assertFalse(sub.is_trial)
        self.assertEqual(sub.mp_preapproval_id, 'pre-123')
        self.assertEqual(sub.mp_status, 'pending')
        self.assertEqual(sub.mp_init_point, resp.data['init_point'])
        self.assertEqual(sub.amount, Decimal('55000.00'))
        self.assertEqual(sub.currency, 'ARS')

    @patch(SDK_PATH)
    def test_preapproval_payload_monthly(self, mock_sdk_class):
        create = mock_sdk_class.return_value.preapproval.return_value.create
        create.return_value = _ok_preapproval()
        self.client.force_login(self.user)

        self._post({'plan': self.monthly.slug})

        payload = create.call_args[0][0]
        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(payload['auto_recurring'], {
            'frequency': 1, 'frequency_type': 'months',
            'transaction_amount': 55000.0, 'currency_id': 'ARS',
        })
        self.assertEqual(payload['external_reference'], str(sub.id))
        self.assertEqual(payload['payer_email'], 'socia@example.com')
        self.assertTrue(payload['back_url'].endswith('/membresia/estado/'))
        self.assertNotIn('free_trial', payload)
        self.assertNotIn('free_trial', payload['auto_recurring'])

    @patch(SDK_PATH)
    def test_preapproval_payload_yearly_is_twelve_months(self, mock_sdk_class):
        create = mock_sdk_class.return_value.preapproval.return_value.create
        create.return_value = _ok_preapproval()
        self.client.force_login(self.user)

        resp = self._post({'plan': self.yearly.slug})

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        recurring = create.call_args[0][0]['auto_recurring']
        self.assertEqual((recurring['frequency'], recurring['frequency_type']), (12, 'months'))
        self.assertEqual(recurring['transaction_amount'], 300000.0)

    @patch(SDK_PATH)
    def test_price_comes_from_db_not_request(self, mock_sdk_class):
        create = mock_sdk_class.return_value.preapproval.return_value.create
        create.return_value = _ok_preapproval()
        self.client.force_login(self.user)

        self._post({
            'plan': self.monthly.slug, 'price': 1, 'amount': 1, 'transaction_amount': 1,
            'currency': 'USD', 'currency_id': 'USD',
        })

        recurring = create.call_args[0][0]['auto_recurring']
        self.assertEqual(recurring['transaction_amount'], 55000.0)
        self.assertEqual(recurring['currency_id'], 'ARS')
        sub = Subscription.objects.get(user=self.user)
        self.assertEqual((sub.amount, sub.currency), (Decimal('55000.00'), 'ARS'))

    @patch(SDK_PATH)
    def test_signup_does_not_grant_video_access(self, mock_sdk_class):
        mock_sdk_class.return_value.preapproval.return_value.create.return_value = _ok_preapproval()
        video = Video.objects.create(
            title='Plan 2', youtube_url='https://youtu.be/dQw4w9WgXcQ', category=self.category,
            is_published=True, access_level='plan2',
        )
        all_paid = Video.objects.create(
            title='Todos', youtube_url='https://youtu.be/dQw4w9WgXcQ', category=self.category,
            is_published=True, access_level='all_paid',
        )
        self.client.force_login(self.user)

        self.assertEqual(self._post({'plan': self.monthly.slug}).status_code, status.HTTP_201_CREATED)

        self.assertFalse(can_access_video(self.user, video))
        self.assertFalse(can_access_video(self.user, all_paid))

    # ── rejections ─────────────────────────────────────────────────────
    @patch(SDK_PATH)
    def test_trial_plan_rejected(self, mock_sdk_class):
        self.client.force_login(self.user)
        resp = self._post({'plan': self.trial_plan.slug})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Subscription.objects.count(), 0)
        mock_sdk_class.assert_not_called()

    @patch(SDK_PATH)
    def test_inactive_plan_rejected(self, mock_sdk_class):
        self.monthly.is_active = False
        self.monthly.save()
        self.client.force_login(self.user)
        resp = self._post({'plan': self.monthly.slug})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Subscription.objects.count(), 0)
        mock_sdk_class.assert_not_called()

    @patch(SDK_PATH)
    def test_unknown_plan_rejected(self, mock_sdk_class):
        self.client.force_login(self.user)
        resp = self._post({'plan': 'no-existe'})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        mock_sdk_class.assert_not_called()

    @patch(SDK_PATH)
    def test_plan_without_known_cadence_rejected(self, mock_sdk_class):
        self.monthly.duration_days = None
        self.monthly.save()
        self.client.force_login(self.user)
        resp = self._post({'plan': self.monthly.slug})
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(Subscription.objects.count(), 0)
        mock_sdk_class.assert_not_called()

    @patch(SDK_PATH)
    def test_user_without_email_rejected(self, mock_sdk_class):
        user = User.objects.create_user(username='sin_email', password='x')
        self.client.force_login(user)
        resp = self._post({'plan': self.monthly.slug})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Subscription.objects.count(), 0)
        mock_sdk_class.assert_not_called()

    @patch(SDK_PATH)
    def test_user_with_active_paid_subscription_rejected(self, mock_sdk_class):
        Subscription.objects.create(
            user=self.user, plan=self.monthly, status=SubscriptionStatus.ACTIVE,
            ends_at=timezone.now() + timedelta(days=20),
        )
        self.client.force_login(self.user)
        resp = self._post({'plan': self.yearly.slug})
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)
        mock_sdk_class.assert_not_called()

    @patch(SDK_PATH)
    def test_anonymous_rejected(self, mock_sdk_class):
        resp = self._post({'plan': self.monthly.slug})
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertEqual(Subscription.objects.count(), 0)
        mock_sdk_class.assert_not_called()

    # ── MP failure ─────────────────────────────────────────────────────
    @patch(SDK_PATH)
    def test_mp_failure_leaves_no_entitled_state_and_trial_intact(self, mock_sdk_class):
        trial = self._start_trial()
        original_ends_at = trial.ends_at
        mock_sdk_class.return_value.preapproval.return_value.create.return_value = FakeMPResponse(
            400, {'message': 'boom'},
        )
        self.client.force_login(self.user)

        resp = self._post({'plan': self.monthly.slug})

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        failed = Subscription.objects.get(user=self.user, is_trial=False)
        self.assertEqual(failed.status, SubscriptionStatus.EXPIRED)
        self.assertFalse(failed.is_active())
        self.assertEqual(failed.mp_preapproval_id, '')
        trial.refresh_from_db()
        self.assertEqual(trial.ends_at, original_ends_at)
        self.assertIsNone(trial.superseded_by)
        self.assertTrue(user_has_active_trial(self.user))

    @patch(SDK_PATH)
    def test_mp_exception_is_handled_the_same_way(self, mock_sdk_class):
        mock_sdk_class.return_value.preapproval.return_value.create.side_effect = ConnectionError()
        self.client.force_login(self.user)

        resp = self._post({'plan': self.monthly.slug})

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertFalse(any(s.is_active() for s in Subscription.objects.filter(user=self.user)))

    @patch(SDK_PATH)
    def test_mp_response_without_init_point_is_a_failure(self, mock_sdk_class):
        mock_sdk_class.return_value.preapproval.return_value.create.return_value = _ok_preapproval(
            init_point=None,
        )
        self.client.force_login(self.user)
        resp = self._post({'plan': self.monthly.slug})
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(Subscription.objects.get(user=self.user).status, SubscriptionStatus.EXPIRED)

    # ── trial is NOT touched at signup ─────────────────────────────────
    @patch(SDK_PATH)
    def test_successful_signup_leaves_active_trial_untouched(self, mock_sdk_class):
        trial = self._start_trial()
        original_ends_at = trial.ends_at
        mock_sdk_class.return_value.preapproval.return_value.create.return_value = _ok_preapproval()
        self.client.force_login(self.user)

        resp = self._post({'plan': self.monthly.slug})

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        trial.refresh_from_db()
        self.assertEqual(trial.ends_at, original_ends_at)
        self.assertIsNone(trial.superseded_by)
        self.assertTrue(trial.is_active())
        self.assertTrue(user_has_active_trial(self.user))

    @patch(SDK_PATH)
    def test_trial_user_is_not_blocked_as_already_subscribed(self, mock_sdk_class):
        self._start_trial()
        mock_sdk_class.return_value.preapproval.return_value.create.return_value = _ok_preapproval()
        self.client.force_login(self.user)
        self.assertEqual(self._post({'plan': self.yearly.slug}).status_code, status.HTTP_201_CREATED)

    # ── duplicate-preapproval guard ────────────────────────────────────
    @patch(SDK_PATH)
    def test_repeat_signup_within_window_reuses_existing_preapproval(self, mock_sdk_class):
        create = mock_sdk_class.return_value.preapproval.return_value.create
        create.return_value = _ok_preapproval()
        self.client.force_login(self.user)

        first = self._post({'plan': self.monthly.slug})
        second = self._post({'plan': self.monthly.slug})

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data['init_point'], first.data['init_point'])
        self.assertEqual(second.data['preapproval_id'], 'pre-123')
        self.assertEqual(create.call_count, 1)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)

    @patch(SDK_PATH)
    def test_pending_signup_older_than_window_is_not_reused(self, mock_sdk_class):
        create = mock_sdk_class.return_value.preapproval.return_value.create
        create.return_value = _ok_preapproval()
        self.client.force_login(self.user)
        self._post({'plan': self.monthly.slug})
        Subscription.objects.filter(user=self.user).update(
            created_at=timezone.now() - PENDING_SIGNUP_REUSE_WINDOW - timedelta(minutes=1),
        )
        create.return_value = _ok_preapproval(id='pre-456')

        resp = self._post({'plan': self.monthly.slug})

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['preapproval_id'], 'pre-456')
        self.assertEqual(create.call_count, 2)

    @patch(SDK_PATH)
    def test_pending_signup_for_other_plan_or_old_price_is_not_reused(self, mock_sdk_class):
        create = mock_sdk_class.return_value.preapproval.return_value.create
        create.return_value = _ok_preapproval()
        self.client.force_login(self.user)
        self._post({'plan': self.monthly.slug})

        create.return_value = _ok_preapproval(id='pre-yearly')
        self.assertEqual(self._post({'plan': self.yearly.slug}).status_code, status.HTTP_201_CREATED)

        self.monthly.price = Decimal('60000.00')
        self.monthly.save()
        create.return_value = _ok_preapproval(id='pre-new-price')
        resp = self._post({'plan': self.monthly.slug})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['preapproval_id'], 'pre-new-price')
        self.assertEqual(create.call_count, 3)


class SupersedeActiveTrialTests(_PlansMixin, TestCase):
    """memberships.services.supersede_active_trial — unwired in 5B-2a;
    5B-2b calls it once MP confirms the paid subscription's charge."""
    def setUp(self):
        self._make_plans()
        self.user = User.objects.create_user(
            username='socia', email='socia@example.com', password='x',
        )
        now = timezone.now()
        self.trial = Subscription.objects.create(
            user=self.user, plan=self.trial_plan, status=SubscriptionStatus.TRIAL,
            starts_at=now, ends_at=now + timedelta(days=7), trial_ends_at=now + timedelta(days=7),
            is_trial=True,
        )
        self.paid = Subscription.objects.create(
            user=self.user, plan=self.monthly, status=SubscriptionStatus.PENDING,
            amount=self.monthly.price, currency='ARS', mp_preapproval_id='pre-123',
        )

    def test_supersedes_active_trial(self):
        original_trial_ends_at = self.trial.trial_ends_at
        result = supersede_active_trial(self.user, self.paid)

        self.assertEqual(result, self.trial)
        self.trial.refresh_from_db()
        self.assertEqual(self.trial.superseded_by, self.paid)
        self.assertLessEqual(self.trial.ends_at, timezone.now())
        self.assertFalse(self.trial.is_active())
        self.assertTrue(self.trial.is_trial)
        self.assertEqual(self.trial.status, SubscriptionStatus.TRIAL)
        self.assertEqual(self.trial.trial_ends_at, original_trial_ends_at)
        self.assertFalse(user_has_active_trial(self.user))

    def test_second_call_is_a_noop(self):
        supersede_active_trial(self.user, self.paid)
        self.trial.refresh_from_db()
        first_ends_at = self.trial.ends_at

        self.assertIsNone(supersede_active_trial(self.user, self.paid))
        self.trial.refresh_from_db()
        self.assertEqual(self.trial.ends_at, first_ends_at)

    def test_expired_trial_is_left_alone(self):
        past = timezone.now() - timedelta(days=1)
        Subscription.objects.filter(pk=self.trial.pk).update(ends_at=past)

        self.assertIsNone(supersede_active_trial(self.user, self.paid))
        self.trial.refresh_from_db()
        self.assertEqual(self.trial.ends_at, past)
        self.assertIsNone(self.trial.superseded_by)

    def test_other_users_trial_is_not_touched(self):
        other = User.objects.create_user(username='otra', email='otra@example.com', password='x')
        now = timezone.now()
        other_trial = Subscription.objects.create(
            user=other, plan=self.trial_plan, status=SubscriptionStatus.TRIAL,
            starts_at=now, ends_at=now + timedelta(days=7), is_trial=True,
        )

        supersede_active_trial(self.user, self.paid)

        other_trial.refresh_from_db()
        self.assertIsNone(other_trial.superseded_by)
        self.assertTrue(other_trial.is_active())


class MembresiaPagesTests(_PlansMixin, TestCase):
    def setUp(self):
        self._make_plans()
        self.user = User.objects.create_user(
            username='socia', email='socia@example.com', password='x',
        )

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(f'/membresia/{self.monthly.slug}/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/ingresar/', resp['Location'])

    def test_eligible_user_sees_subscribe_button(self):
        self.client.force_login(self.user)
        resp = self.client.get(f'/membresia/{self.yearly.slug}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-subscribe-form')
        self.assertContains(resp, 'por año')
        self.assertContains(resp, 'csrfmiddlewaretoken')

    def test_trial_user_warned_trial_ends_on_payment_confirmation(self):
        now = timezone.now()
        Subscription.objects.create(
            user=self.user, plan=self.trial_plan, status=SubscriptionStatus.TRIAL,
            starts_at=now, ends_at=now + timedelta(days=7), is_trial=True,
        )
        self.client.force_login(self.user)
        resp = self.client.get(f'/membresia/{self.monthly.slug}/')
        self.assertContains(resp, 'data-subscribe-form')
        self.assertContains(resp, 'hasta que Mercado Pago confirme el pago')

    def test_subscribed_user_sees_message_not_button(self):
        Subscription.objects.create(
            user=self.user, plan=self.monthly, status=SubscriptionStatus.ACTIVE,
            ends_at=timezone.now() + timedelta(days=20),
        )
        self.client.force_login(self.user)
        resp = self.client.get(f'/membresia/{self.yearly.slug}/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'data-subscribe-form')
        self.assertContains(resp, 'Ya tenés una membresía activa')

    def test_trial_plan_and_inactive_plan_404(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/membresia/{self.trial_plan.slug}/').status_code, 404)
        self.monthly.is_active = False
        self.monthly.save()
        self.assertEqual(self.client.get(f'/membresia/{self.monthly.slug}/').status_code, 404)

    def test_estado_page_shows_pending_and_changes_nothing(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.monthly, status=SubscriptionStatus.PENDING,
            mp_preapproval_id='pre-123',
        )
        self.client.force_login(self.user)
        resp = self.client.get('/membresia/estado/?preapproval_id=pre-123&status=authorized')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Estamos confirmando tu suscripción')
        sub.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatus.PENDING)


class StartSubscriptionCsrfTests(_PlansMixin, APITestCase):
    """SessionAuthentication — and its CSRF check — stays in effect, same
    as CheckoutInitiationView: a logged-in POST without a token is
    refused before anything is created or sent to MP."""
    def setUp(self):
        self._make_plans()
        self.user = User.objects.create_user(
            username='socia', email='socia@example.com', password='x',
        )

    @patch(SDK_PATH)
    def test_session_post_without_csrf_token_rejected(self, mock_sdk_class):
        from rest_framework.test import APIClient
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        resp = client.post('/api/subscribe/', {'plan': self.monthly.slug}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Subscription.objects.count(), 0)
        mock_sdk_class.assert_not_called()
