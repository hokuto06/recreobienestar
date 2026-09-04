from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from site_content.models import ContactMessage, Offering, SiteSettings, Testimonial


class SiteSettingsApiTests(APITestCase):
    def test_returns_the_singleton_even_if_never_saved(self):
        # site_content's 0003 migration seeds the singleton on every fresh
        # DB (mirrors real production, where the row already exists) —
        # clear it here so this test can still exercise the actual
        # "never saved" path it's named for.
        SiteSettings.objects.all().delete()
        self.assertEqual(SiteSettings.objects.count(), 0)
        resp = self.client.get(reverse('site-settings'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['tagline'], '')

    def test_returns_saved_content(self):
        settings_obj = SiteSettings.load()
        settings_obj.tagline = 'El éxito no tiene por qué costarte tu cuerpo'
        settings_obj.carla_bio = 'Párrafo uno.\n\nPárrafo dos.'
        settings_obj.carla_bio_highlight = 'ReCREO, me permite expresar mi esencia.'
        settings_obj.save()
        resp = self.client.get(reverse('site-settings'))
        self.assertEqual(resp.data['tagline'], 'El éxito no tiene por qué costarte tu cuerpo')
        self.assertIn('Párrafo uno.', resp.data['carla_bio'])
        self.assertEqual(resp.data['carla_bio_highlight'], 'ReCREO, me permite expresar mi esencia.')

    def test_write_methods_not_allowed(self):
        for verb in ('post', 'put', 'patch', 'delete'):
            resp = getattr(self.client, verb)(reverse('site-settings'), {'tagline': 'Hackeado'})
            self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class OfferingApiTests(APITestCase):
    def setUp(self):
        self.active = Offering.objects.create(
            name='Curso Neuro Postural', price=55000, currency='ARS',
            payment_url_ars='https://mpago.la/example', is_active=True,
        )
        self.inactive = Offering.objects.create(
            name='Descontinuado', price=1000, is_active=False,
        )

    def test_only_active_offerings_returned(self):
        resp = self.client.get(reverse('offering-list'))
        names = [o['name'] for o in resp.data['results']]
        self.assertIn('Curso Neuro Postural', names)
        self.assertNotIn('Descontinuado', names)

    def test_payment_links_exposed(self):
        resp = self.client.get(reverse('offering-list'))
        item = next(o for o in resp.data['results'] if o['slug'] == self.active.slug)
        self.assertEqual(item['payment_url_ars'], 'https://mpago.la/example')
        self.assertEqual(item['payment_url_usd'], '')

    def test_write_methods_not_allowed(self):
        for verb in ('post', 'put', 'patch', 'delete'):
            resp = getattr(self.client, verb)(reverse('offering-list'), {'name': 'Hackeado'})
            self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertFalse(Offering.objects.filter(name='Hackeado').exists())


class TestimonialApiTests(APITestCase):
    def setUp(self):
        self.active = Testimonial.objects.create(
            author_name='Reseña Activa', text='Una reseña de prueba.', rating=5, is_active=True,
        )
        self.inactive = Testimonial.objects.create(
            author_name='Reseña Inactiva', text='Oculta por Carla.', rating=2, is_active=False,
        )

    def test_only_active_testimonials_returned(self):
        resp = self.client.get(reverse('testimonial-list'))
        names = [t['author_name'] for t in resp.data['results']]
        self.assertIn('Reseña Activa', names)
        self.assertNotIn('Reseña Inactiva', names)

    def test_is_active_not_exposed(self):
        # is_active es un toggle interno (Carla oculta/muestra desde el
        # Admin), no contenido de marketing — nunca debe viajar en la
        # respuesta pública, a diferencia de rating/text/author_name.
        resp = self.client.get(reverse('testimonial-list'))
        item = next(t for t in resp.data['results'] if t['author_name'] == 'Reseña Activa')
        self.assertNotIn('is_active', item)

    def test_write_methods_not_allowed(self):
        for verb in ('post', 'put', 'patch', 'delete'):
            resp = getattr(self.client, verb)(reverse('testimonial-list'), {'author_name': 'Hackeado'})
            self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertFalse(Testimonial.objects.filter(author_name='Hackeado').exists())


class TestimonialSeedMigrationTests(APITestCase):
    def test_five_seeded_testimonials_present_on_a_fresh_database(self):
        # No objects created manually in this test — relies purely on
        # migration 0005_seed_testimonials, which runs when the test DB
        # is built. Validates the seed migration end-to-end, same as
        # SiteSettingsApiTests validates 0003 by relying on its seed.
        resp = self.client.get(reverse('testimonial-list'))
        self.assertEqual(len(resp.data['results']), 5)


class ContactMessageApiTests(APITestCase):
    def setUp(self):
        # ContactMessageCreateView is throttled (5/hour per IP, see
        # settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']) via Django's
        # cache framework, which — unlike the DB — isn't reset between
        # tests by Django's test runner. Clearing it here gives every test
        # in this class its own fresh throttle window, regardless of
        # execution order or how many other tests ran first.
        from django.core.cache import cache
        cache.clear()

    def test_valid_submission_saves_a_row(self):
        resp = self.client.post(reverse('contact-message-create'), {
            'name': 'Ana', 'email': 'ana@example.com', 'message': 'Quiero más info.',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ContactMessage.objects.count(), 1)
        saved = ContactMessage.objects.get()
        self.assertEqual(saved.name, 'Ana')
        self.assertEqual(saved.email, 'ana@example.com')
        self.assertEqual(saved.message, 'Quiero más info.')
        self.assertFalse(saved.is_read)

    def test_missing_fields_rejected(self):
        resp = self.client.post(reverse('contact-message-create'), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('name', resp.data)
        self.assertIn('email', resp.data)
        self.assertIn('message', resp.data)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_invalid_email_rejected(self):
        resp = self.client.post(reverse('contact-message-create'), {
            'name': 'Ana', 'email': 'no-es-un-email', 'message': 'Hola',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', resp.data)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_blank_message_rejected(self):
        resp = self.client.post(reverse('contact-message-create'), {
            'name': 'Ana', 'email': 'ana@example.com', 'message': '   ',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('message', resp.data)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_honeypot_filled_does_not_save_but_looks_like_success(self):
        resp = self.client.post(reverse('contact-message-create'), {
            'name': 'Bot', 'email': 'bot@example.com', 'message': 'spam',
            'website': 'http://spam.example.com',
        }, format='json')
        # Same response shape as a real success — a bot gets no signal
        # that it was caught.
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_is_read_not_writable(self):
        resp = self.client.post(reverse('contact-message-create'), {
            'name': 'Ana', 'email': 'ana@example.com', 'message': 'Hola', 'is_read': True,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertFalse(ContactMessage.objects.get().is_read)

    def test_endpoint_does_not_require_authentication(self):
        # self.client is a fresh, unauthenticated APIClient — no login/
        # force_login/force_authenticate anywhere in this test — so a 201
        # here proves the endpoint accepts a fully anonymous request
        # instead of silently requiring a session.
        resp = self.client.post(reverse('contact-message-create'), {
            'name': 'Anon', 'email': 'anon@example.com', 'message': 'Sin sesión.',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse('contact-message-create'))
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
