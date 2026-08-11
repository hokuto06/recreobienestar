from django.core.exceptions import ValidationError
from django.test import TestCase

from memberships.models import MembershipPlan


class MembershipPlanValidationTests(TestCase):
    def test_negative_price_rejected(self):
        plan = MembershipPlan(tier='plan1', name='Plan Básico', price=-100)
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_zero_price_allowed(self):
        plan = MembershipPlan(tier='plan1', name='Plan Gratuito Promocional', price=0)
        plan.full_clean()  # Should not raise.

    def test_positive_price_allowed(self):
        plan = MembershipPlan(tier='plan1', name='Plan Básico', price=1500)
        plan.full_clean()  # Should not raise.

    def test_slug_auto_generated(self):
        plan = MembershipPlan.objects.create(tier='plan1', name='Plan Premium', price=2000)
        self.assertEqual(plan.slug, 'plan-premium')

    def test_default_active(self):
        plan = MembershipPlan.objects.create(tier='plan1', name='Plan X', price=100)
        self.assertTrue(plan.is_active)

    def test_third_tier_is_valid(self):
        # Phase 3.7 extended PlanTier to 3 independent tiers (Columna Sana:
        # Lumbar/Profundo/Integrador) — plan3 must be as valid as plan1/2.
        plan = MembershipPlan.objects.create(tier='plan3', name='Plan Integrador', price=45000)
        plan.full_clean()  # Should not raise: 'plan3' is a real PlanTier choice.
        self.assertEqual(plan.tier, 'plan3')

    def test_presentation_fields_default_blank(self):
        # New Phase 3.7 fields are all optional — creating a plan the old
        # way (just tier/name/price) must keep working unchanged.
        plan = MembershipPlan.objects.create(tier='plan1', name='Plan X', price=100)
        self.assertEqual(plan.subtitle, '')
        self.assertEqual(plan.badge, '')
        self.assertEqual(plan.visual_variant, 'default')
        self.assertEqual(plan.cta_label, '')


class MembershipPlanActivationTests(TestCase):
    def setUp(self):
        self.plan = MembershipPlan.objects.create(
            tier='plan2', name='Plan Full', price=3000, is_active=True,
        )

    def test_deactivate_plan(self):
        self.plan.is_active = False
        self.plan.save()
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.is_active)

    def test_reactivate_plan(self):
        self.plan.is_active = False
        self.plan.save()
        self.plan.is_active = True
        self.plan.save()
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.is_active)

    def test_deactivating_plan_does_not_touch_existing_subscriptions(self):
        from django.contrib.auth import get_user_model
        from memberships.models import Subscription

        user = get_user_model().objects.create_user(username='ana', password='x')
        sub = Subscription.objects.create(user=user, plan=self.plan, status='active')

        self.plan.is_active = False
        self.plan.save()

        sub.refresh_from_db()
        self.assertEqual(sub.status, 'active')
