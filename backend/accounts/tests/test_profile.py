"""Regression tests for the 2026-08-10 production bug: uploading a real
profile avatar caused an Internal Server Error (settings.STORAGES had no
'default' entry), and — masked until that was fixed — the resulting file
had no URL route to actually serve it in production. See config/settings.py
and config/urls.py for the fixes and their reasoning."""
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Profile

User = get_user_model()

# Real files land on disk regardless of the test transaction rollback (only
# the DB row is undone) -- isolate them to a throwaway directory instead of
# writing into backend/media/ on every test run.
_TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix='recreo_test_media_')


def _tiny_png():
    """A real, minimal, valid PNG — ImageField validates file content via
    Pillow (Image.open), not just the filename/extension, so arbitrary
    bytes won't pass form validation the way a real upload would."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (1, 1), color='white').save(buffer, format='PNG')
    buffer.seek(0)
    return SimpleUploadedFile('avatar.png', buffer.read(), content_type='image/png')


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class ProfileAvatarUploadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user(username='conimagen', password='x')
        self.url = reverse('accounts:profile_edit')

    def test_avatar_upload_does_not_500(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            self.url,
            {'display_name': 'Con Imagen', 'avatar': _tiny_png()},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 302)

    def test_avatar_upload_is_saved_on_profile(self):
        self.client.force_login(self.user)
        self.client.post(self.url, {'display_name': 'Con Imagen', 'avatar': _tiny_png()})
        profile = Profile.objects.get(user=self.user)
        self.assertTrue(profile.avatar)
        self.assertTrue(profile.avatar.name.startswith('avatars/'))

    def test_uploaded_avatar_is_servable(self):
        self.client.force_login(self.user)
        self.client.post(self.url, {'display_name': 'Con Imagen', 'avatar': _tiny_png()})
        profile = Profile.objects.get(user=self.user)
        resp = self.client.get(profile.avatar.url)
        self.assertEqual(resp.status_code, 200)

    def test_profile_edit_without_avatar_still_works(self):
        """The bug only triggers when a FileField is actually written --
        confirm the plain text-only path (already covered indirectly
        elsewhere) still works after the STORAGES change."""
        self.client.force_login(self.user)
        resp = self.client.post(self.url, {'display_name': 'Sin Imagen'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Profile.objects.get(user=self.user).display_name, 'Sin Imagen')
