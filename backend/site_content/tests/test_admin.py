from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from site_content.models import SiteSettings

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
