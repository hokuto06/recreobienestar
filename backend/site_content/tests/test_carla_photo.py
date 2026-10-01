"""SiteSettings.carla_photo: the "Sobre Carla" photo, editable from the
Admin, exposed as a site-relative URL by /api/site-settings/, seeded once
with `manage.py set_carla_photo`, and optional — the static home page
keeps its "C" placeholder whenever it's unset."""
import io
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APITestCase

from site_content.models import SiteSettings

REPO_ROOT = Path(settings.BASE_DIR).parent


def _jpeg_bytes(size=(800, 1000)):
    buf = io.BytesIO()
    Image.new('RGB', size, (200, 160, 140)).save(buf, 'JPEG')
    return buf.getvalue()


class _TempMediaMixin:
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)


class CarlaPhotoFieldTests(_TempMediaMixin, TestCase):
    def test_field_is_optional_and_uploads_to_site_carla(self):
        field = SiteSettings._meta.get_field('carla_photo')
        self.assertTrue(field.blank)
        self.assertEqual(field.upload_to, 'site/carla/')
        self.assertFalse(SiteSettings.load().carla_photo)

    def test_admin_form_offers_the_field(self):
        admin = get_user_model().objects.create_superuser('admin', 'a@example.com', 'x')
        self.client.force_login(admin)
        obj = SiteSettings.load()
        resp = self.client.get(reverse('admin:site_content_sitesettings_change', args=[obj.pk]))
        self.assertContains(resp, 'name="carla_photo"')
        self.assertContains(resp, 'al menos 800 px de ancho')


class CarlaPhotoApiTests(_TempMediaMixin, APITestCase):
    def test_url_is_null_when_no_photo(self):
        resp = self.client.get(reverse('site-settings'))
        self.assertIn('carla_photo_url', resp.data)
        self.assertIsNone(resp.data['carla_photo_url'])

    def test_url_is_site_relative_media_path_when_set(self):
        obj = SiteSettings.load()
        obj.carla_photo = SimpleUploadedFile('carla.jpg', _jpeg_bytes(), content_type='image/jpeg')
        obj.save()
        url = self.client.get(reverse('site-settings')).data['carla_photo_url']
        self.assertTrue(url.startswith('/media/site/carla/carla'), url)
        self.assertNotIn('://', url)
        # ...and the URL actually serves the image through the media route.
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/jpeg')


class SetCarlaPhotoCommandTests(_TempMediaMixin, TestCase):
    def test_sets_photo_from_file(self):
        src = Path(self.media_root) / 'source.jpeg'
        src.write_bytes(_jpeg_bytes())
        call_command('set_carla_photo', str(src), stdout=io.StringIO())
        photo = SiteSettings.load().carla_photo
        self.assertTrue(photo.name.startswith('site/carla/source'))
        self.assertTrue((Path(self.media_root) / photo.name).is_file())

    def test_rejects_missing_or_non_image_file(self):
        with self.assertRaises(CommandError):
            call_command('set_carla_photo', '/nonexistent/carla.jpeg')
        bogus = Path(self.media_root) / 'not-an-image.jpeg'
        bogus.write_text('hola')
        with self.assertRaises(CommandError):
            call_command('set_carla_photo', str(bogus))
        self.assertFalse(SiteSettings.load().carla_photo)


class HomePagePlaceholderTests(TestCase):
    """The static home page (repo-root index.html + js/home-dynamic.js,
    served by nginx) keeps the "C" placeholder in its markup and only
    swaps it for the photo once the image has loaded — so an unset or
    broken photo still shows the placeholder, never a broken image."""

    def test_markup_keeps_placeholder_inside_photo_frame(self):
        html = (REPO_ROOT / 'index.html').read_text(encoding='utf-8')
        start = html.index('data-carla-photo>')
        self.assertIn('<span class="avatar-mono">C</span>', html[start:start + 120])

    def test_script_swaps_only_on_load_and_only_internal_paths(self):
        js = (REPO_ROOT / 'js' / 'home-dynamic.js').read_text(encoding='utf-8')
        block = js[js.index('settings.carla_photo_url'):js.index('if (settings.carla_bio_highlight)')]
        self.assertIn("photoUrl.charAt(0) === '/'", block)
        self.assertIn("photoUrl.charAt(1) !== '/'", block)
        onload = block[block.index('photo.onload'):block.index('photo.src')]
        self.assertIn('replaceChildren(photo)', onload)
