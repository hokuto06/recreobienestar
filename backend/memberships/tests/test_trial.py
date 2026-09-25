"""Phase 5B-1: the free 7-day trial — POST /api/trial/ (StartTrialView)
and GET /prueba-gratis/ (memberships.public_views.prueba_gratis)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from memberships.models import MembershipPlan, Subscription

User = get_user_model()


class StartTrialViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='nueva_socia', password='x')
        self.trial_plan = MembershipPlan.objects.create(
            tier='plan1', name='Plan 1', subtitle='FREE TRIAL', price=0,
            trial_days=7, is_active=True,
        )
        self.paid_plan = MembershipPlan.objects.create(
            tier='plan2', name='Plan 2', price=5000, is_active=True,
        )

    def _post(self):
        return self.client.post(reverse('start-trial'))

    def test_authenticated_user_starts_trial_successfully(self):
        self.client.force_login(self.user)
        resp = self._post()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(sub.plan, self.trial_plan)
        self.assertEqual(sub.status, 'trial')
        self.assertTrue(sub.is_trial)
        self.assertIsNotNone(sub.trial_ends_at)
        self.assertEqual(sub.ends_at, sub.trial_ends_at)
        self.assertAlmostEqual(
            sub.ends_at, timezone.now() + timedelta(days=7), delta=timedelta(seconds=5),
        )

    def test_anonymous_rejected_no_subscription_created(self):
        resp = self._post()
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertEqual(Subscription.objects.count(), 0)

    def test_second_trial_attempt_rejected(self):
        self.client.force_login(self.user)
        first = self._post()
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self._post()
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Subscription.objects.filter(user=self.user, is_trial=True).count(), 1)

    def test_second_trial_attempt_rejected_after_first_expired(self):
        # is_trial=True is what's checked, regardless of status/ends_at —
        # an already-lapsed trial still counts as "used".
        Subscription.objects.create(
            user=self.user, plan=self.trial_plan, status='trial',
            ends_at=timezone.now() - timedelta(days=1), is_trial=True,
        )
        self.client.force_login(self.user)
        resp = self._post()
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)

    def test_user_with_active_paid_subscription_rejected(self):
        Subscription.objects.create(
            user=self.user, plan=self.paid_plan, status='active',
            ends_at=timezone.now() + timedelta(days=20),
        )
        self.client.force_login(self.user)
        resp = self._post()
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(Subscription.objects.filter(user=self.user, is_trial=True).exists())

    def test_db_constraint_prevents_a_second_trial_row(self):
        # The actual race-safety mechanism (see Subscription.Meta.
        # constraints): even bypassing the view's own upfront .exists()
        # check entirely, the DB itself refuses a second is_trial=True
        # row for the same user — this is what makes two concurrent
        # double-submits safe, not just the check-then-create ordering.
        Subscription.objects.create(
            user=self.user, plan=self.trial_plan, status='trial',
            ends_at=timezone.now() + timedelta(days=7), is_trial=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subscription.objects.create(
                    user=self.user, plan=self.trial_plan, status='trial',
                    ends_at=timezone.now() + timedelta(days=7), is_trial=True,
                )
        self.assertEqual(Subscription.objects.filter(user=self.user, is_trial=True).count(), 1)


class PruebaGratisPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='nueva_socia', password='x')
        self.trial_plan = MembershipPlan.objects.create(
            tier='plan1', name='Plan 1', subtitle='FREE TRIAL', price=0,
            trial_days=7, is_active=True,
        )

    def _url(self):
        return reverse('memberships:prueba_gratis')

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/ingresar/', resp.url)
        self.assertIn(self._url(), resp.url)

    def test_eligible_user_sees_start_button(self):
        self.client.force_login(self.user)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Empezar prueba')
        self.assertContains(resp, 'csrfmiddlewaretoken')

    def test_user_who_already_used_trial_sees_message_not_button(self):
        Subscription.objects.create(
            user=self.user, plan=self.trial_plan, status='trial',
            ends_at=timezone.now() - timedelta(days=1), is_trial=True,
        )
        self.client.force_login(self.user)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Empezar prueba')
        self.assertContains(resp, 'Ya usaste tu prueba gratuita')
