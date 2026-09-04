from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from memberships.models import MembershipPlan


class MembershipPlanApiTests(APITestCase):
    def setUp(self):
        self.active = MembershipPlan.objects.create(
            tier='plan1', name='Plan Activo', price=1000, is_active=True,
        )
        self.inactive = MembershipPlan.objects.create(
            tier='plan2', name='Plan Desactivado', price=2000, is_active=False,
        )

    def test_only_active_plans_returned(self):
        resp = self.client.get(reverse('plan-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [p['name'] for p in resp.data['results']]
        self.assertIn('Plan Activo', names)
        self.assertNotIn('Plan Desactivado', names)

    def test_internal_fields_not_exposed(self):
        resp = self.client.get(reverse('plan-list'))
        plan_data = resp.data['results'][0]
        for field in ('is_active', 'created_at', 'updated_at'):
            self.assertNotIn(field, plan_data)

    def test_write_methods_not_allowed(self):
        url = reverse('plan-list')
        for verb in ('post', 'put', 'patch', 'delete'):
            resp = getattr(self.client, verb)(url, {'name': 'Hackeado'})
            self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertFalse(MembershipPlan.objects.filter(name='Hackeado').exists())

    def test_response_is_paginated(self):
        resp = self.client.get(reverse('plan-list'))
        self.assertIn('count', resp.data)
        self.assertIn('results', resp.data)

    def test_presentation_fields_exposed(self):
        # Phase 3.7: the visual-reference fields (subtitle/badge/
        # visual_variant/cta_label) must reach the API — home-dynamic.js
        # can't render the approved card design without them.
        self.active.subtitle = 'Alivio Lumbar'
        self.active.badge = 'Más recomendado'
        self.active.visual_variant = 'highlighted'
        self.active.cta_label = 'Empezar alivio'
        self.active.save()
        resp = self.client.get(reverse('plan-list'))
        plan_data = next(p for p in resp.data['results'] if p['name'] == 'Plan Activo')
        self.assertEqual(plan_data['subtitle'], 'Alivio Lumbar')
        self.assertEqual(plan_data['badge'], 'Más recomendado')
        self.assertEqual(plan_data['visual_variant'], 'highlighted')
        self.assertEqual(plan_data['cta_label'], 'Empezar alivio')
