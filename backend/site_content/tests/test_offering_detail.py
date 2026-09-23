"""Phase 4B-3: GET /propuestas/<slug>/ — the Django-rendered page that
lets a logged-in member start a real Mercado Pago checkout. Mirrors
catalog/tests/test_program_detail.py's shape for its sibling view."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from site_content.models import Offering

User = get_user_model()


class OfferingDetailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='compradora', password='x')
        self.offering = Offering.objects.create(
            name='Curso Neuro Postural', description='Un curso completo.',
            price=55000, currency='ARS', is_active=True,
        )

    def _url(self):
        return reverse('site_content:offering_detail', args=[self.offering.slug])

    def test_anonymous_visitor_redirected_to_login_with_next(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/ingresar/', resp.url)
        self.assertIn(self._url(), resp.url)

    def test_logged_in_user_sees_offering_200(self):
        self.client.force_login(self.user)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.offering.name)
        self.assertContains(resp, self.offering.description)
        self.assertContains(resp, '55000')
        self.assertContains(resp, 'ARS')
        self.assertContains(resp, 'Comprar')
        # The buy button's form must carry a real CSRF token — this page
        # is exactly what makes the token reachable to site.js's fetch().
        self.assertContains(resp, 'csrfmiddlewaretoken')
        self.assertContains(resp, 'data-offering-slug="' + self.offering.slug + '"')

    def test_unknown_slug_404s(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('site_content:offering_detail', args=['no-existe']))
        self.assertEqual(resp.status_code, 404)

    def test_inactive_offering_404s(self):
        self.offering.is_active = False
        self.offering.save()
        self.client.force_login(self.user)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 404)
