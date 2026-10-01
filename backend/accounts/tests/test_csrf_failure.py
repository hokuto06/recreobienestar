"""CSRF_FAILURE_VIEW (common.views.csrf_failure): a stale or missing CSRF
token still gets a 403, but on a friendly, site-styled page that echoes
nothing from the request except a validated internal "back" path."""
from django.test import Client, TestCase, override_settings

FORM = {
    'email': 'eco-test@example.com',
    'password1': 'SecretoNoEcho123',
    'password2': 'SecretoNoEcho123',
}
BAD_TOKEN = 'x' * 64


class CsrfFailurePageTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        # A real CSRF cookie, so the failure is "token incorrect" — the
        # production case (form left open across a login in another tab).
        self.client.get('/registro/')

    def _post(self, **extra):
        return self.client.post('/registro/', {**FORM, 'csrfmiddlewaretoken': BAD_TOKEN}, **extra)

    def _assert_friendly_403(self, resp):
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, '403_csrf.html')
        self.assertTemplateUsed(resp, 'base.html')
        self.assertContains(resp, 'La página quedó desactualizada', status_code=403)
        self.assertContains(resp, 'Tus datos no se enviaron', status_code=403)

    def test_bad_token_renders_friendly_page_with_403(self):
        self._assert_friendly_403(self._post())

    @override_settings(DEBUG=False)
    def test_renders_with_debug_false(self):
        self._assert_friendly_403(self._post())

    @override_settings(DEBUG=True)
    def test_renders_with_debug_true(self):
        self._assert_friendly_403(self._post())

    def test_missing_cookie_also_gets_friendly_page(self):
        resp = Client(enforce_csrf_checks=True).post('/registro/', FORM)
        self._assert_friendly_403(resp)

    def test_does_not_echo_form_data_token_or_reason(self):
        resp = self._post()
        body = resp.content.decode()
        for leaked in ('eco-test@example.com', 'SecretoNoEcho123', BAD_TOKEN, 'CSRF token'):
            self.assertNotIn(leaked, body)

    def test_same_site_referer_gives_back_link_path_only(self):
        resp = self._post(HTTP_REFERER='http://testserver/registro/?email=eco-test@example.com#x')
        self.assertContains(resp, 'href="/registro/"', status_code=403)
        self.assertNotContains(resp, 'eco-test@example.com', status_code=403)

    def test_external_or_missing_referer_falls_back_to_home_and_login(self):
        for extra in ({'HTTP_REFERER': 'https://evil.example/phish'},
                      {'HTTP_REFERER': '//evil.example/x'},
                      {'HTTP_REFERER': 'javascript:alert(1)'},
                      {}):
            resp = self._post(**extra)
            self.assertNotContains(resp, 'evil.example', status_code=403)
            self.assertNotContains(resp, 'javascript:', status_code=403)
            self.assertNotContains(resp, 'Volver a la página anterior', status_code=403)
            self.assertContains(resp, 'href="/ingresar/"', status_code=403)
