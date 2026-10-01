"""GET /api/session/ (accounts.views.SessionStateView): tells the static
home page's nav whether there's a member session — and nothing else. It's
the one per-visitor /api/ response, so it must never be cacheable."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class SessionStateTests(TestCase):
    def setUp(self):
        self.url = reverse('session-state')

    def _assert_no_store(self, resp):
        cache_control = resp.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)
        self.assertIn('private', cache_control)
        self.assertNotIn('public', cache_control)

    def test_url(self):
        self.assertEqual(self.url, '/api/session/')

    def test_anonymous(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'authenticated': False})
        self._assert_no_store(resp)

    def test_authenticated_exposes_only_the_flag(self):
        user = User.objects.create_user(
            username='ana', email='ana-secreta@example.com', password='x',
            first_name='Ana', last_name='Pérez',
        )
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'authenticated': True})
        body = resp.content.decode()
        for leaked in ('ana-secreta', 'example.com', 'Ana', 'Pérez', str(user.pk)):
            self.assertNotIn(leaked, body)
        self._assert_no_store(resp)

    def test_after_logout_is_anonymous(self):
        user = User.objects.create_user(username='beto', password='x')
        self.client.force_login(user)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).json(), {'authenticated': False})

    def test_varies_on_cookie(self):
        # Belt and braces for any cache that does honor Vary.
        self.assertIn('Cookie', self.client.get(self.url).get('Vary', ''))

    def test_get_only(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)


class SessionNginxConfigTests(TestCase):
    """nginx strips Django's Cache-Control (server-level proxy_hide_header),
    so the no-store that actually reaches CloudFront must come from nginx's
    own exact-match location for this path."""

    def test_nginx_sets_no_store_for_session_endpoint(self):
        conf = (Path(settings.BASE_DIR) / 'nginx' / 'conf.d' / 'recreobienestar.conf').read_text()
        start = conf.index('location = /api/session/ {')
        block = conf[start:conf.index('}', start)]
        self.assertIn('add_header Cache-Control "no-store, private, max-age=0" always;', block)
        self.assertIn('include /etc/nginx/snippets/seguridad.conf;', block)
        self.assertIn('proxy_pass http://$django_upstream;', block)
