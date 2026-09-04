from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from site_content.models import ContactMessage, SiteSettings, Testimonial

User = get_user_model()


class SiteSettingsAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='carla_admin', password='x', is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)

    def test_changelist_redirects_to_the_singleton_edit_form(self):
        resp = self.client.get(reverse('admin:site_content_sitesettings_changelist'))
        self.assertEqual(resp.status_code, 302)
        obj = SiteSettings.load()
        self.assertIn(str(obj.pk), resp.url)

    def test_changelist_creates_the_row_if_missing(self):
        # site_content's 0003 migration seeds the singleton on every fresh
        # DB (mirrors real production, where the row already exists) —
        # clear it here so this test can still exercise the actual
        # "missing row" path it's named for.
        SiteSettings.objects.all().delete()
        self.assertEqual(SiteSettings.objects.count(), 0)
        self.client.get(reverse('admin:site_content_sitesettings_changelist'))
        self.assertEqual(SiteSettings.objects.count(), 1)

    def test_edit_form_saves_correctly(self):
        obj = SiteSettings.load()
        resp = self.client.post(
            reverse('admin:site_content_sitesettings_change', args=[obj.pk]),
            {
                'tagline': 'Nueva frase', 'hero_headline': '', 'carla_bio': '',
                'contact_email': '', 'instagram_url': '', 'podcast_name': '', 'podcast_url': '',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SiteSettings.load().tagline, 'Nueva frase')
        self.assertEqual(SiteSettings.objects.count(), 1)


class TestimonialAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='carla_admin2', password='x', is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)
        self.testimonial = Testimonial.objects.create(author_name='María', text='Reseña', rating=5)

    def test_changelist_loads(self):
        resp = self.client.get(reverse('admin:site_content_testimonial_changelist'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'María')

    def test_display_order_and_is_active_are_list_editable(self):
        # Carla reordena/oculta reseñas inline desde el changelist, sin
        # entrar al form de edición de cada una.
        resp = self.client.post(
            reverse('admin:site_content_testimonial_changelist'),
            {
                'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '1',
                'form-MIN_NUM_FORMS': '0', 'form-MAX_NUM_FORMS': '1000',
                'form-0-id': str(self.testimonial.pk),
                'form-0-display_order': '3',
                '_save': 'Save',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.testimonial.refresh_from_db()
        self.assertEqual(self.testimonial.display_order, 3)
        self.assertFalse(self.testimonial.is_active)  # checkbox omitted = unchecked


class ContactMessageAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='carla_admin3', password='x', is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)
        self.msg = ContactMessage.objects.create(name='Ana', email='ana@example.com', message='Consulta')

    def test_changelist_loads(self):
        resp = self.client.get(reverse('admin:site_content_contactmessage_changelist'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ana')

    def test_add_permission_disabled(self):
        # Los mensajes solo se crean desde POST /api/contacto/, nunca a
        # mano desde el Admin.
        resp = self.client.get(reverse('admin:site_content_contactmessage_add'))
        self.assertEqual(resp.status_code, 403)

    def test_is_read_is_list_editable(self):
        resp = self.client.post(
            reverse('admin:site_content_contactmessage_changelist'),
            {
                'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '1',
                'form-MIN_NUM_FORMS': '0', 'form-MAX_NUM_FORMS': '1000',
                'form-0-id': str(self.msg.pk),
                'form-0-is_read': 'on',
                '_save': 'Save',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.msg.refresh_from_db()
        self.assertTrue(self.msg.is_read)
