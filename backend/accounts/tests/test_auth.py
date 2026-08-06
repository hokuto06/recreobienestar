from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import Profile

User = get_user_model()


class RegistrationTests(TestCase):
    def test_registration_creates_user_and_profile(self):
        resp = self.client.post(reverse('accounts:register'), {
            'email': 'nueva@example.com',
            'display_name': 'Nueva Miembra',
            'password1': 'ContraseñaSegura123!',
            'password2': 'ContraseñaSegura123!',
        })
        self.assertRedirects(resp, reverse('accounts:dashboard'))
        user = User.objects.get(email='nueva@example.com')
        self.assertTrue(Profile.objects.filter(user=user, display_name='Nueva Miembra').exists())

    def test_registration_logs_the_user_in(self):
        self.client.post(reverse('accounts:register'), {
            'email': 'auto-login@example.com',
            'password1': 'ContraseñaSegura123!',
            'password2': 'ContraseñaSegura123!',
        })
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_duplicate_email_rejected(self):
        User.objects.create_user(username='existente', email='ya@example.com', password='x')
        resp = self.client.post(reverse('accounts:register'), {
            'email': 'ya@example.com',
            'password1': 'ContraseñaSegura123!',
            'password2': 'ContraseñaSegura123!',
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors, not redirected
        self.assertContains(resp, 'Ya existe una cuenta')

    def test_weak_password_rejected(self):
        resp = self.client.post(reverse('accounts:register'), {
            'email': 'debil@example.com',
            'password1': '12345678',
            'password2': '12345678',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email='debil@example.com').exists())

    def test_form_errors_are_aria_wired_to_their_field(self):
        # A screen reader needs the error programmatically associated with
        # the field, not just visually adjacent to it.
        resp = self.client.post(reverse('accounts:register'), {
            'email': 'not-an-email',
            'password1': 'x', 'password2': 'y',
        })
        content = resp.content.decode()
        self.assertIn('aria-describedby="email-hint"', content)
        self.assertIn('id="email-hint"', content)
        self.assertIn('aria-invalid="true"', content)


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='miembra1', email='miembra1@example.com', password='ClaveSegura123!',
        )

    def test_login_with_username(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'miembra1', 'password': 'ClaveSegura123!',
        })
        self.assertRedirects(resp, reverse('accounts:dashboard'))

    def test_login_with_email(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'miembra1@example.com', 'password': 'ClaveSegura123!',
        })
        self.assertRedirects(resp, reverse('accounts:dashboard'))

    def test_login_redirects_to_next_when_present(self):
        # A user bounced to /ingresar/ from a protected page should land
        # back there after logging in, not always on the dashboard.
        profile_url = reverse('accounts:profile_edit')
        resp = self.client.post(
            f"{reverse('accounts:login')}?next={profile_url}",
            {'username': 'miembra1', 'password': 'ClaveSegura123!', 'next': profile_url},
        )
        self.assertRedirects(resp, profile_url)

    def test_login_ignores_unsafe_next(self):
        resp = self.client.post(
            reverse('accounts:login'),
            {
                'username': 'miembra1', 'password': 'ClaveSegura123!',
                'next': 'https://evil.example.com/',
            },
        )
        # Django's own open-redirect guard falls back to LOGIN_REDIRECT_URL
        # for any `next` that isn't a safe same-site URL.
        self.assertRedirects(resp, reverse('accounts:dashboard'))

    def test_login_wrong_password_shows_clear_error(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'miembra1', 'password': 'incorrecta',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No encontramos una cuenta')

    def test_logout_requires_post(self):
        self.client.login(username='miembra1', password='ClaveSegura123!')
        resp = self.client.get(reverse('accounts:logout'))
        self.assertEqual(resp.status_code, 405)

    def test_logout_clears_session(self):
        self.client.login(username='miembra1', password='ClaveSegura123!')
        self.client.post(reverse('accounts:logout'))
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertRedirects(resp, f"{reverse('accounts:login')}?next={reverse('accounts:dashboard')}")


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='resetme', email='resetme@example.com', password='ClaveVieja123!',
        )

    def test_password_reset_request_sends_email(self):
        resp = self.client.post(reverse('accounts:password_reset'), {'email': 'resetme@example.com'})
        self.assertRedirects(resp, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('resetme@example.com', mail.outbox[0].to)

    def test_password_reset_request_unknown_email_does_not_leak(self):
        resp = self.client.post(reverse('accounts:password_reset'), {'email': 'nadie@example.com'})
        # Same redirect either way — doesn't reveal whether the account exists.
        self.assertRedirects(resp, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)


class ProtectedRouteTests(TestCase):
    def test_dashboard_redirects_anonymous_user(self):
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertRedirects(resp, f"{reverse('accounts:login')}?next={reverse('accounts:dashboard')}")

    def test_profile_edit_redirects_anonymous_user(self):
        resp = self.client.get(reverse('accounts:profile_edit'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp.url)
