"""Monthly -> Annual upgrade (memberships/upgrades.py).

Order under test: authorize the annual (first charge a day before the
monthly's next charge) -> annual's first charge confirmed -> ONLY THEN cancel
the monthly. Access must never lapse; MP failures must leave a coherent
state; double submits must be harmless. MP SDK always mocked."""
import re
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from common.choices import SubscriptionStatus
from memberships.models import Subscription, SubscriptionCharge, SubscriptionChargeOutcome
from memberships.services import RENEWAL_MARGIN, can_access_video, paid_period_end
from memberships.tests.test_subscription_webhooks import CANCEL_SDK, SUB_SDK, _Fixtures
from memberships.upgrades import ANNUAL_CHARGE_LEAD, get_upgrade_offer
from payments.tests.test_checkout import FakeMPResponse
from payments.tests.test_webhook import WEBHOOK_SECRET, WEBHOOK_URL, _signature_header
from site_content.models import SiteSettings

CARLA = 'carla@example.com'
MEMBER = 'socia@example.com'
UPGRADE_URL = '/api/subscription/upgrade/'


def _to(address):
    return [m for m in mail.outbox if m.to == [address]]


class _Upgrade(_Fixtures):
    def _base(self):
        self._setup()
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': CARLA})
        now = timezone.now()
        self.monthly = self.sub
        self.monthly.status = SubscriptionStatus.ACTIVE
        self.monthly.mp_status = 'authorized'
        self.monthly.starts_at = now - timedelta(days=12)
        self.monthly.next_payment_date = now + timedelta(days=18)
        self.monthly.ends_at = self.monthly.next_payment_date + RENEWAL_MARGIN
        self.monthly.save()
        self.renewal_at = self.monthly.next_payment_date
        self.monthly_ends_at = self.monthly.ends_at

    def _mp_create_ok(self, mock_views_sdk, preapproval_id='pre-annual'):
        create = mock_views_sdk.return_value.preapproval.return_value.create
        create.return_value = FakeMPResponse(201, {
            'id': preapproval_id, 'status': 'pending',
            'init_point': f'https://www.mercadopago.com.ar/subscriptions/checkout?preapproval_id={preapproval_id}',
        })
        return create

    def _annual(self):
        return Subscription.objects.filter(replaces=self.monthly).order_by('-id').first()


class UpgradeEligibilityTests(_Upgrade, TestCase):
    def setUp(self):
        self._base()
        self.client.force_login(self.user)

    def test_monthly_member_sees_the_offer_with_everything_before_confirming(self):
        offer = get_upgrade_offer(self.user)
        self.assertTrue(offer.can_upgrade)
        self.assertEqual(offer.renewal_at, self.renewal_at)
        self.assertEqual(offer.annual_charge_at, self.renewal_at - ANNUAL_CHARGE_LEAD)
        resp = self.client.get(reverse('memberships:mi_suscripcion'))
        renewal_day = timezone.localtime(self.renewal_at).strftime('%d/%m/%Y')
        charge_day = timezone.localtime(offer.annual_charge_at).strftime('%d/%m/%Y')
        for expected in (
            'data-upgrade-card', 'Pasate al Plan Anual',
            f'Mantenés tu plan mensual y todo tu acceso hasta el <strong>{renewal_day}</strong>',
            'Ese día empieza tu Plan Anual', '300.000 ARS por año',
            'Hoy no se te cobra nada.', f'hace el primer cobro anual el {charge_day}',
            'no pagás dos veces ni perdés ningún día', 'data-upgrade-form',
        ):
            self.assertContains(resp, expected)
        self.assertContains(resp, 'Plan Mensual')  # the running monthly is still the one shown
        self.assertContains(self.client.get(reverse('accounts:dashboard')), 'data-upgrade-link')

    def test_not_offered_to_annual_trial_or_unpaid_members(self):
        cases = {}
        annual_user = self.user.__class__.objects.create_user(username='anual', email='a@example.com', password='x')
        annual = self._pending(self.yearly, user=annual_user, preapproval_id='pre-y')
        self._make_active(annual)
        Subscription.objects.filter(pk=annual.pk).update(mp_status='authorized')
        cases['annual'] = annual_user
        trial_user = self.user.__class__.objects.create_user(username='prueba', email='t@example.com', password='x')
        self._trial(user=trial_user)
        cases['trial'] = trial_user
        cases['nothing'] = self.user.__class__.objects.create_user(username='nada', email='n@example.com', password='x')
        pending_user = self.user.__class__.objects.create_user(username='pend', email='p@example.com', password='x')
        self._pending(self.monthly.plan, user=pending_user, preapproval_id='pre-p')
        cases['pending monthly'] = pending_user
        for label, user in cases.items():
            with self.subTest(label):
                self.assertFalse(get_upgrade_offer(user).can_upgrade)
                self.client.force_login(user)
                self.assertNotContains(self.client.get(reverse('accounts:dashboard')), 'data-upgrade-link')
                self.assertEqual(self.client.post(UPGRADE_URL, {}, content_type='application/json').status_code, 409)

    def test_monthly_in_grace_is_not_offered(self):
        self.monthly.grace_ends_at = timezone.now() + timedelta(days=3)
        self.monthly.save()
        self.assertIsNone(get_upgrade_offer(self.user).monthly)

    @patch(CANCEL_SDK)
    def test_too_close_to_renewal_explains_why_and_what_to_do(self, mock_sdk):
        self.monthly.next_payment_date = timezone.now() + timedelta(days=2)
        self.monthly.save()
        day = timezone.localtime(self.monthly.next_payment_date).strftime('%d/%m/%Y')
        expected = (
            f'Tu renovación mensual es el {day}. Para no cobrarte dos veces, el cambio al plan '
            f'anual tiene que quedar listo unos días antes, así que ahora no se puede: podés '
            f'pasarte al anual después del {day}.'
        )
        resp = self.client.get(reverse('memberships:mi_suscripcion'))
        self.assertContains(resp, 'data-upgrade-refusal')
        self.assertContains(resp, f'Tu renovación mensual es el {day}')
        self.assertNotContains(resp, 'data-upgrade-form')
        api = self.client.post(UPGRADE_URL, {}, content_type='application/json')
        self.assertEqual(api.status_code, 409)
        self.assertEqual(api.json()['detail'], expected)
        mock_sdk.return_value.preapproval.return_value.create.assert_not_called()


class UpgradeRequestTests(_Upgrade, APITestCase):
    def setUp(self):
        self._base()
        self.client.force_login(self.user)

    @patch(CANCEL_SDK)
    def test_creates_the_annual_with_a_future_start_date_and_leaves_the_monthly_alone(self, mock_sdk):
        create = self._mp_create_ok(mock_sdk)
        resp = self.client.post(UPGRADE_URL, {}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertIn('pre-annual', resp.json()['init_point'])
        annual = self._annual()
        self.assertEqual(annual.status, SubscriptionStatus.PENDING)
        self.assertEqual(annual.plan, self.yearly)
        self.assertEqual(annual.amount, Decimal('300000.00'))
        self.assertEqual(annual.starts_at, self.renewal_at - ANNUAL_CHARGE_LEAD)
        data = create.call_args.args[0]
        self.assertEqual(data['external_reference'], f'sub-{annual.id}')
        self.assertEqual(data['auto_recurring']['frequency'], 12)
        self.assertEqual(data['auto_recurring']['frequency_type'], 'months')
        self.assertEqual(data['auto_recurring']['transaction_amount'], 300000.0)
        self.assertTrue(re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000[+-]\d\d:\d\d$', data['auto_recurring']['start_date']))
        self.assertLessEqual(len(data['reason']), 60)
        mock_sdk.return_value.preapproval.return_value.update.assert_not_called()  # monthly untouched
        self.monthly.refresh_from_db()
        self.assertEqual((self.monthly.status, self.monthly.mp_status), (SubscriptionStatus.ACTIVE, 'authorized'))

    @patch(CANCEL_SDK)
    def test_double_submit_creates_one_preapproval(self, mock_sdk):
        create = self._mp_create_ok(mock_sdk)
        first = self.client.post(UPGRADE_URL, {}, format='json')
        second = self.client.post(UPGRADE_URL, {}, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['init_point'], first.json()['init_point'])
        self.assertEqual(create.call_count, 1)
        self.assertEqual(Subscription.objects.filter(replaces=self.monthly).count(), 1)
        # Once authorized, a third submit is refused: already scheduled.
        Subscription.objects.filter(replaces=self.monthly).update(mp_status='authorized')
        third = self.client.post(UPGRADE_URL, {}, format='json')
        self.assertEqual(third.status_code, 409)
        self.assertIn('ya está programado', third.json()['detail'])
        self.assertEqual(create.call_count, 1)

    @patch(CANCEL_SDK)
    def test_mp_failure_creating_the_annual_leaves_everything_as_it_was(self, mock_sdk):
        mock_sdk.return_value.preapproval.return_value.create.return_value = FakeMPResponse(500, {'message': 'x'})
        resp = self.client.post(UPGRADE_URL, {}, format='json')
        self.assertEqual(resp.status_code, 502)
        self.assertIn('No se cambió nada: seguís con tu plan mensual', resp.json()['detail'])
        self.assertEqual(self._annual().status, SubscriptionStatus.EXPIRED)
        self.monthly.refresh_from_db()
        self.assertEqual((self.monthly.status, self.monthly.ends_at), (SubscriptionStatus.ACTIVE, self.monthly_ends_at))
        self.assertTrue(self.monthly.is_active())
        self.assertTrue(get_upgrade_offer(self.user).can_upgrade)  # can simply try again


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class UpgradeLifecycleTests(_Upgrade, APITestCase):
    """Through the real webhook endpoint, from request to activation."""

    def setUp(self):
        self._base()
        self.client.force_login(self.user)
        with patch(CANCEL_SDK) as mock_sdk:
            self._mp_create_ok(mock_sdk)
            self.client.post(UPGRADE_URL, {}, format='json')
        self.annual = self._annual()

    def _notify(self, notification_type, data_id):
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                f'{WEBHOOK_URL}?data.id={data_id}&type={notification_type}', data={}, format='json',
                headers={'x-request-id': 'r', 'x-signature': _signature_header(data_id, 'r', '1700000000')},
            )
        self.monthly.refresh_from_db()
        self.annual.refresh_from_db()
        return resp

    def _authorize(self, mock_sdk):
        mock_sdk.return_value.preapproval.return_value.get.return_value = FakeMPResponse(200, {
            'id': 'pre-annual', 'status': 'authorized', 'external_reference': f'sub-{self.annual.id}',
            'next_payment_date': timezone.localtime(self.annual.starts_at).isoformat(),
        })
        return self._notify('subscription_preapproval', 'pre-annual')

    def _annual_charge(self, mock_sdk, status='approved', payment_id='pay-annual-1', ap_id='ap-annual-1'):
        mock_sdk.return_value.invoice.return_value.get.return_value = FakeMPResponse(200, {
            'id': ap_id, 'preapproval_id': 'pre-annual', 'type': 'recurring', 'status': 'processed',
            'transaction_amount': 300000, 'currency_id': 'ARS', 'external_reference': f'sub-{self.annual.id}',
            'payment': {'id': payment_id, 'status': status},
        })
        return self._notify('subscription_authorized_payment', ap_id)

    def _cancel_calls(self, mock_sdk):
        return [c.args[0] for c in mock_sdk.return_value.preapproval.return_value.update.call_args_list]

    @patch(SUB_SDK)
    def test_authorization_emails_the_member_once_and_changes_nothing_else(self, mock_sdk):
        self._authorize(mock_sdk)
        self._authorize(mock_sdk)  # MP's duplicate notification
        self.assertEqual(self.annual.status, SubscriptionStatus.PENDING)
        self.assertEqual(self.monthly.status, SubscriptionStatus.ACTIVE)
        emails = _to(MEMBER)
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].subject, 'Tu cambio al Plan Anual quedó programado')
        day = timezone.localtime(self.renewal_at).strftime('%d/%m/%Y')
        for expected in (f'hasta el {day}', '300.000,00 ARS por año', 'Hoy no se te cobró nada',
                         'no se te cobra dos veces'):
            self.assertIn(expected, emails[0].body)
        self.assertEqual(self._cancel_calls(mock_sdk), [])

    @patch(SUB_SDK)
    def test_approved_first_charge_activates_the_annual_then_cancels_the_monthly(self, mock_sdk):
        self._authorize(mock_sdk)
        mock_sdk.return_value.preapproval.return_value.update.return_value = FakeMPResponse(200, {'status': 'cancelled'})
        self._annual_charge(mock_sdk)
        self.assertEqual(self.annual.status, SubscriptionStatus.ACTIVE)
        # 12 months counted from the end of the monthly's paid month: no day lost.
        self.assertEqual(self.annual.ends_at, paid_period_end(self.yearly, self.renewal_at))
        self.assertEqual(self._cancel_calls(mock_sdk), ['pre-1'])  # the MONTHLY's preapproval
        self.assertEqual(self.monthly.status, SubscriptionStatus.CANCELLED)
        self.assertEqual(self.monthly.ends_at, self.monthly_ends_at)  # its access is kept
        buyer = [m for m in _to(MEMBER) if m.subject == 'Tu Plan Anual ya está activo']
        self.assertEqual(len(buyer), 1)
        self.assertIn('no perdés ningún día de acceso', buyer[0].body)
        carla = _to(CARLA)
        self.assertEqual(len(carla), 1)
        self.assertTrue(carla[0].subject.startswith('Cambio a plan anual: Plan Anual'))

    @patch(SUB_SDK)
    def test_duplicate_approved_notification_cancels_nothing_twice(self, mock_sdk):
        self._authorize(mock_sdk)
        mock_sdk.return_value.preapproval.return_value.update.return_value = FakeMPResponse(200, {'status': 'cancelled'})
        self._annual_charge(mock_sdk)
        self._annual_charge(mock_sdk)
        self._annual_charge(mock_sdk, ap_id='ap-annual-1-again')
        self.assertEqual(self._cancel_calls(mock_sdk), ['pre-1'])
        self.assertEqual(len(_to(CARLA)), 1)

    @patch(SUB_SDK)
    def test_failed_first_charge_cancels_the_annual_and_keeps_the_monthly(self, mock_sdk):
        self._authorize(mock_sdk)
        mock_sdk.return_value.preapproval.return_value.update.return_value = FakeMPResponse(200, {'status': 'cancelled'})
        self._annual_charge(mock_sdk, status='rejected')
        self.assertEqual(self._cancel_calls(mock_sdk), ['pre-annual'])  # the ANNUAL, never the monthly
        self.assertEqual(self.annual.status, SubscriptionStatus.EXPIRED)
        self.assertEqual(self.monthly.status, SubscriptionStatus.ACTIVE)
        self.assertTrue(self.monthly.is_active())
        failed = [m for m in _to(MEMBER) if m.subject == 'No pudimos activar tu Plan Anual']
        self.assertEqual(len(failed), 1)
        self.assertIn('seguís con tu plan mensual como siempre', failed[0].body)

    @patch(SUB_SDK)
    def test_mp_failing_to_cancel_the_monthly_is_retried_then_alerts_carla_once(self, mock_sdk):
        self._authorize(mock_sdk)
        update = mock_sdk.return_value.preapproval.return_value.update
        update.return_value = FakeMPResponse(500, {'message': 'MP down'})
        with self.assertLogs('memberships.upgrades', level='ERROR'):
            self._annual_charge(mock_sdk)
        self.assertEqual(self.annual.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(self.monthly.status, SubscriptionStatus.ACTIVE)  # not cancelled, still entitled
        mail.outbox.clear()

        def reconcile():
            with self.captureOnCommitCallbacks(execute=True):
                call_command('reconcile_subscription_charges', stdout=StringIO())
            self.monthly.refresh_from_db()

        # Far from the monthly's next charge: retried, no alert yet.
        with self.assertLogs('memberships.upgrades', level='ERROR'):
            reconcile()
        self.assertEqual(_to(CARLA), [])
        # Within 12h of the monthly's next charge and still failing: urgent alert, once.
        Subscription.objects.filter(pk=self.monthly.pk).update(next_payment_date=timezone.now() + timedelta(hours=6))
        with self.assertLogs('memberships.upgrades', level='ERROR'):
            reconcile()
        with self.assertLogs('memberships.upgrades', level='ERROR'):
            reconcile()
        alerts = _to(CARLA)
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].subject.startswith('URGENTE: cancelar a mano el plan mensual'))
        self.assertIn('pre-1', alerts[0].body)
        self.assertIsNotNone(self.monthly.upgrade_cancel_alerted_at)
        # MP back: the next reconcile run cancels it.
        update.return_value = FakeMPResponse(200, {'status': 'cancelled'})
        reconcile()
        self.assertEqual(self.monthly.status, SubscriptionStatus.CANCELLED)
        self.assertEqual(self.monthly.ends_at, self.monthly_ends_at)

    @patch(SUB_SDK)
    def test_mp_failing_to_cancel_a_failed_annual_is_retried(self, mock_sdk):
        self._authorize(mock_sdk)
        update = mock_sdk.return_value.preapproval.return_value.update
        update.return_value = FakeMPResponse(500, {'message': 'MP down'})
        with self.assertLogs('memberships.upgrades', level='ERROR'):
            self._annual_charge(mock_sdk, status='rejected')
        self.assertEqual(self.annual.status, SubscriptionStatus.PENDING)
        self.assertEqual(self.monthly.status, SubscriptionStatus.ACTIVE)
        update.return_value = FakeMPResponse(200, {'status': 'cancelled'})
        mock_sdk.return_value.invoice.return_value.search.return_value = FakeMPResponse(200, {'results': []})
        with self.captureOnCommitCallbacks(execute=True):
            call_command('reconcile_subscription_charges', stdout=StringIO())
        self.annual.refresh_from_db()
        self.assertEqual(self.annual.status, SubscriptionStatus.EXPIRED)
        self.assertEqual(len([m for m in _to(MEMBER) if m.subject == 'No pudimos activar tu Plan Anual']), 1)

    @patch(SUB_SDK)
    def test_access_never_lapses_at_any_point(self, mock_sdk):
        """Checks access at every step AND at every future instant up to a
        year out, for both the success and the failure path."""
        video = self.plan2_video  # a plan2 (Monthly-tier) video
        all_paid = self.plan2_video.__class__.objects.create(
            title='Todo', youtube_url='https://youtu.be/dQw4w9WgXcQ', category=self.category,
            is_published=True, access_level='all_paid',
        )

        def entitled(at):
            subs = list(self.user.subscriptions.select_related('plan'))
            return any(s.is_active(at=at) for s in subs)

        def assert_continuous(until):
            moment = timezone.now()
            while moment < until:
                self.assertTrue(entitled(moment), f'access gap at {moment}')
                moment += timedelta(hours=6)

        self.assertTrue(can_access_video(self.user, video))           # after the request
        self._authorize(mock_sdk)
        self.assertTrue(can_access_video(self.user, video))           # after authorization
        assert_continuous(self.monthly_ends_at)                       # monthly alone carries it
        mock_sdk.return_value.preapproval.return_value.update.return_value = FakeMPResponse(200, {})
        self._annual_charge(mock_sdk)
        self.assertTrue(can_access_video(self.user, all_paid))        # after activation + monthly cancel
        assert_continuous(self.annual.ends_at - timedelta(hours=1))   # monthly then annual, no gap
        self.assertGreater(self.annual.ends_at, self.monthly_ends_at)

    @patch(SUB_SDK)
    def test_access_never_lapses_when_the_annual_fails(self, mock_sdk):
        self._authorize(mock_sdk)
        mock_sdk.return_value.preapproval.return_value.update.return_value = FakeMPResponse(200, {})
        self._annual_charge(mock_sdk, status='rejected')
        self.assertTrue(self.monthly.is_active())
        self.assertTrue(self.monthly.is_active(at=self.monthly_ends_at - timedelta(minutes=1)))
        self.assertEqual(self.monthly.status, SubscriptionStatus.ACTIVE)  # MP renews it as usual


class UpgradeDiscoverabilityTests(_Upgrade, TestCase):
    """The upgrade is reachable from the dashboard card and the annual plan
    page — for eligible members only; everyone else sees what they saw."""
    OLD_COPY = 'Ya tenés una membresía activa. Por ahora no se puede cambiar de plan.'

    def setUp(self):
        self._base()
        self.annual_page = reverse('memberships:membresia_detail', args=[self.yearly.slug])
        self.monthly_page = reverse('memberships:membresia_detail', args=[self.monthly.plan.slug])

    def _login(self, user=None):
        self.client.force_login(user or self.user)

    def _user(self, name):
        return self.user.__class__.objects.create_user(username=name, email=f'{name}@example.com', password='x')

    def test_eligible_member_sees_the_cta_on_the_dashboard_card(self):
        self._login()
        resp = self.client.get(reverse('accounts:dashboard'))
        day = timezone.localtime(self.renewal_at).strftime('%d/%m/%Y')
        for expected in (
            'class="upgrade-callout" data-upgrade-link', 'Pasate al Plan Anual',
            f'Seguís con tu plan mensual hasta el {day}', '300.000 ARS por año', 'Hoy no se cobra nada.',
            'href="/mi-cuenta/suscripcion/#pasar-al-anual"', 'Ver el cambio al Plan Anual',
        ):
            self.assertContains(resp, expected)
        # "Administrar suscripción" is now a real (ghost) button.
        self.assertContains(resp, 'class="btn btn-ghost btn-sm btn-block"')
        self.assertContains(resp, 'data-manage-subscription>Administrar suscripción</a>')
        self.assertContains(self.client.get(reverse('memberships:mi_suscripcion')), 'id="pasar-al-anual"')

    def test_eligible_member_on_the_annual_page_is_sent_to_the_upgrade(self):
        self._login()
        resp = self.client.get(self.annual_page)
        self.assertContains(resp, 'data-upgrade-plan-page')
        self.assertContains(resp, 'podés pasarte al Plan Anual sin perder ningún día')
        self.assertContains(resp, 'hoy no se cobra nada')
        self.assertContains(resp, '#pasar-al-anual">Pasarme al Plan Anual</a>')
        self.assertNotContains(resp, self.OLD_COPY)
        self.assertNotContains(resp, 'data-subscribe-form')

    def test_monthly_member_on_the_monthly_page_keeps_the_old_copy(self):
        self._login()
        resp = self.client.get(self.monthly_page)
        self.assertContains(resp, self.OLD_COPY)
        self.assertNotContains(resp, 'data-upgrade-plan-page')

    def test_annual_member_sees_neither_and_keeps_the_old_copy(self):
        user = self._user('anual')
        annual = self._pending(self.yearly, user=user, preapproval_id='pre-y')
        self._make_active(annual)
        self._login(user)
        dashboard = self.client.get(reverse('accounts:dashboard'))
        self.assertNotContains(dashboard, 'upgrade-callout')
        self.assertContains(dashboard, 'data-manage-subscription')
        page = self.client.get(self.annual_page)
        self.assertContains(page, self.OLD_COPY)
        self.assertNotContains(page, 'data-upgrade-plan-page')

    def test_trial_user_sees_no_upgrade_and_can_still_subscribe(self):
        user = self._user('prueba')
        self._trial(user=user)
        self._login(user)
        self.assertNotContains(self.client.get(reverse('accounts:dashboard')), 'upgrade-callout')
        page = self.client.get(self.annual_page)
        self.assertNotContains(page, 'data-upgrade-plan-page')
        self.assertNotContains(page, self.OLD_COPY)
        self.assertContains(page, 'data-subscribe-form')

    def test_cancelled_monthly_still_entitled_is_not_eligible(self):
        # The production case from the diagnosis: cancelled, still has access.
        self.monthly.status = SubscriptionStatus.CANCELLED
        self.monthly.save()
        self._login()
        self.assertNotContains(self.client.get(reverse('accounts:dashboard')), 'upgrade-callout')
        page = self.client.get(self.annual_page)
        self.assertContains(page, self.OLD_COPY)
        self.assertNotContains(page, 'data-upgrade-plan-page')

    def test_refusal_window_explains_on_the_annual_page_and_hides_the_dashboard_cta(self):
        self.monthly.next_payment_date = timezone.now() + timedelta(days=2)
        self.monthly.save()
        self._login()
        self.assertNotContains(self.client.get(reverse('accounts:dashboard')), 'upgrade-callout')
        page = self.client.get(self.annual_page)
        self.assertContains(page, 'data-upgrade-plan-page')
        self.assertContains(page, 'Tu renovación mensual es el')
        self.assertNotContains(page, 'Pasarme al Plan Anual')
        self.assertNotContains(page, self.OLD_COPY)

    def test_scheduled_upgrade_says_so_on_the_annual_page(self):
        Subscription.objects.create(
            user=self.user, plan=self.yearly, status=SubscriptionStatus.PENDING, replaces=self.monthly,
            starts_at=self.renewal_at - ANNUAL_CHARGE_LEAD, amount=self.yearly.price, currency='ARS',
            mp_preapproval_id='pre-annual', mp_status='authorized',
        )
        self._login()
        self.assertNotContains(self.client.get(reverse('accounts:dashboard')), 'upgrade-callout')
        page = self.client.get(self.annual_page)
        self.assertContains(page, 'Tu cambio al Plan Anual ya está programado')
        self.assertNotContains(page, 'Pasarme al Plan Anual')
