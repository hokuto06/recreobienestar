"""Own-hosted posters for locked videos (Video.poster, catalog/posters.py).

The rule under test: a LOCKED video may show our own copy of its poster,
served from /media/ under a random name — and nothing anywhere in a locked
response (list API, detail API, locked page, any card) may contain its
YouTube id or a URL derived from it. YouTube is always mocked; files go to a
temporary MEDIA_ROOT."""
import re
import shutil
import socket
import tempfile
import urllib.error
from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalog.models import Category, Video, video_poster_upload_to
from catalog.posters import POSTER_FETCH_TIMEOUT, fetch_youtube_poster
from common.choices import SubscriptionStatus
from memberships.models import MembershipPlan, Subscription

User = get_user_model()

URLOPEN = 'catalog.posters.urllib.request.urlopen'
LOCKED_ID = 'dQw4w9WgXcQ'
FREE_ID = 'jNQXAC9IVRw'
OPAQUE_NAME = re.compile(r'^videos/posters/[0-9a-f]{32}\.(jpg|jpeg|png|webp)$')


def _jpeg_bytes(color=(120, 90, 60)):
    out = BytesIO()
    Image.new('RGB', (64, 36), color).save(out, format='JPEG')
    return out.getvalue()


def _youtube_returns(mock_urlopen, data=None):
    response = MagicMock()
    response.read.return_value = data if data is not None else _jpeg_bytes()
    mock_urlopen.return_value.__enter__.return_value = response
    return mock_urlopen


class _TempMedia:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix='recreo-posters-')
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()


class _Videos(_TempMedia):
    def setUp(self):
        self.category = Category.objects.create(name='Pilates')
        MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        self.locked = Video.objects.create(
            title='Clase exclusiva', youtube_url=f'https://youtu.be/{LOCKED_ID}',
            category=self.category, is_published=True, access_level='plan1',
        )
        self.locked.poster.save('cualquier-nombre.jpg', ContentFile(_jpeg_bytes()), save=True)
        self.locked_no_poster = Video.objects.create(
            title='Clase sin portada', youtube_url='https://youtu.be/aaaaaaaaaaa',
            category=self.category, is_published=True, access_level='plan1',
        )
        self.free = Video.objects.create(
            title='Clase libre', youtube_url=f'https://youtu.be/{FREE_ID}',
            category=self.category, is_published=True, access_level='free',
        )

    def _item(self, resp, video):
        return next(v for v in resp.data['results'] if v['slug'] == video.slug)

    def _assert_no_locked_id(self, content):
        for video in (self.locked, self.locked_no_poster):
            self.assertNotIn(video.youtube_video_id, content)
            self.assertNotIn(f'img.youtube.com/vi/{video.youtube_video_id}', content)


class PosterApiTests(_Videos, TestCase):
    def test_locked_video_with_poster_returns_the_own_hosted_url(self):
        item = self._item(self.client.get(reverse('video-list')), self.locked)
        self.assertTrue(item['is_locked'])
        self.assertEqual(item['thumbnail'], self.locked.poster.url)
        self.assertTrue(item['thumbnail'].startswith('/media/videos/posters/'))

    def test_poster_url_does_not_contain_the_youtube_id(self):
        item = self._item(self.client.get(reverse('video-list')), self.locked)
        self.assertNotIn(LOCKED_ID, item['thumbnail'])
        self.assertNotIn('youtube', item['thumbnail'])
        self.assertRegex(self.locked.poster.name, OPAQUE_NAME)

    def test_locked_video_without_poster_returns_null(self):
        item = self._item(self.client.get(reverse('video-list')), self.locked_no_poster)
        self.assertTrue(item['is_locked'])
        self.assertIsNone(item['thumbnail'])

    def test_whole_list_response_never_contains_a_locked_video_id(self):
        resp = self.client.get(reverse('video-list'))
        self._assert_no_locked_id(resp.content.decode())

    def test_locked_detail_endpoint_still_403_without_id_or_poster(self):
        resp = self.client.get(reverse('video-detail', args=[self.locked.slug]))
        self.assertEqual(resp.status_code, 403)
        self._assert_no_locked_id(resp.content.decode())

    def test_unlocked_videos_keep_their_current_thumbnail(self):
        item = self._item(self.client.get(reverse('video-list')), self.free)
        self.assertFalse(item['is_locked'])
        self.assertEqual(item['thumbnail'], self.free.thumbnail_display_url)

    def test_member_with_access_gets_the_normal_thumbnail_not_the_poster(self):
        user = User.objects.create_user(username='socia', password='x')
        Subscription.objects.create(
            user=user, plan=MembershipPlan.objects.get(tier='plan1'), status=SubscriptionStatus.ACTIVE,
            ends_at=timezone.now() + timedelta(days=10),
        )
        self.client.force_login(user)
        item = self._item(self.client.get(reverse('video-list')), self.locked)
        self.assertFalse(item['is_locked'])
        self.assertEqual(item['thumbnail'], self.locked.thumbnail_display_url)


class PosterHtmlTests(_Videos, TestCase):
    def test_videoteca_grid_shows_the_poster_under_the_padlock(self):
        resp = self.client.get(reverse('catalog:video_library'))
        content = resp.content.decode()
        self.assertIn(f'class="video-poster" style="background-image:url(\'{self.locked.poster.url}\')"', content)
        self.assertIn('video-locked', content)
        self.assertIn('#icon-lock', content)
        self._assert_no_locked_id(content)

    def test_dashboard_locked_section_shows_the_poster(self):
        self.client.force_login(User.objects.create_user(username='gratis', password='x'))
        resp = self.client.get(reverse('accounts:dashboard'))
        content = resp.content.decode()
        self.assertIn('Contenido bloqueado', content)
        self.assertIn(self.locked.poster.url, content)
        self._assert_no_locked_id(content)

    def test_locked_page_shows_the_poster(self):
        resp = self.client.get(reverse('catalog:video_detail', args=[self.locked.slug]))
        self.assertEqual(resp.status_code, 403)
        content = resp.content.decode()
        self.assertIn(self.locked.poster.url, content)
        self._assert_no_locked_id(content)

    def test_locked_page_without_poster_keeps_the_plain_padlock(self):
        resp = self.client.get(reverse('catalog:video_detail', args=[self.locked_no_poster.slug]))
        content = resp.content.decode()
        self.assertNotIn('video-poster', content)
        self.assertIn('#icon-lock', content)
        self._assert_no_locked_id(content)

    def test_card_without_poster_keeps_the_tinted_placeholder(self):
        resp = self.client.get(reverse('catalog:video_library'))
        content = resp.content.decode()
        self.assertEqual(content.count('class="video-poster"'), 1)  # only the video that has one


class PosterNamingTests(TestCase):
    def test_admin_upload_name_is_opaque_and_keeps_only_the_extension(self):
        name = video_poster_upload_to(None, f'mi-portada-{LOCKED_ID}.PNG')
        self.assertRegex(name, OPAQUE_NAME)
        self.assertTrue(name.endswith('.png'))
        self.assertNotIn(LOCKED_ID, name)

    def test_unknown_extension_falls_back_to_jpg(self):
        self.assertTrue(video_poster_upload_to(None, 'archivo.exe').endswith('.jpg'))


class PosterFetchTests(_TempMedia, TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Pilates')

    def _video(self, youtube_id=LOCKED_ID, **extra):
        return Video.objects.create(
            title=f'Clase {youtube_id}', youtube_url=f'https://youtu.be/{youtube_id}',
            category=self.category, is_published=True, access_level='plan1', **extra,
        )

    @patch(URLOPEN)
    def test_fetch_stores_a_reencoded_copy_under_an_opaque_name(self, mock_urlopen):
        _youtube_returns(mock_urlopen)
        video = self._video()
        fetch_youtube_poster(video)
        video.refresh_from_db()
        self.assertRegex(video.poster.name, OPAQUE_NAME)
        self.assertNotIn(LOCKED_ID, video.poster.name)
        with video.poster.open('rb') as fh, Image.open(fh) as image:
            self.assertEqual(image.format, 'JPEG')
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f'https://img.youtube.com/vi/{LOCKED_ID}/hqdefault.jpg')
        self.assertEqual(mock_urlopen.call_args.kwargs['timeout'], POSTER_FETCH_TIMEOUT)

    @patch(URLOPEN)
    def test_backfill_command_is_idempotent(self, mock_urlopen):
        _youtube_returns(mock_urlopen)
        a, b = self._video(), self._video('bbbbbbbbbbb')
        out = StringIO()
        call_command('fetch_video_posters', stdout=out)
        self.assertIn('2 fetched', out.getvalue())
        a.refresh_from_db()
        first_name = a.poster.name
        self.assertEqual(mock_urlopen.call_count, 2)

        out = StringIO()
        call_command('fetch_video_posters', stdout=out)
        self.assertIn('0 fetched, 2 already had one', out.getvalue())
        self.assertEqual(mock_urlopen.call_count, 2)  # no new downloads
        a.refresh_from_db()
        self.assertEqual(a.poster.name, first_name)

        call_command('fetch_video_posters', '--force', stdout=StringIO())
        self.assertEqual(mock_urlopen.call_count, 4)
        b.refresh_from_db()
        self.assertTrue(b.poster)

    @patch(URLOPEN)
    def test_backfill_reports_failures_and_carries_on(self, mock_urlopen):
        good = _jpeg_bytes()
        response = MagicMock()
        response.read.return_value = good
        mock_urlopen.side_effect = [urllib.error.URLError('down'), MagicMock(__enter__=MagicMock(return_value=response))]
        failing, working = self._video(), self._video('bbbbbbbbbbb')
        out, err = StringIO(), StringIO()
        call_command('fetch_video_posters', stdout=out, stderr=err)
        self.assertIn('1 fetched', out.getvalue())
        self.assertIn('1 failed', out.getvalue())
        self.assertIn('FAILED', err.getvalue())
        failing.refresh_from_db()
        working.refresh_from_db()
        self.assertFalse(failing.poster)
        self.assertTrue(working.poster)

    @override_settings(VIDEO_POSTER_AUTO_FETCH=True)
    @patch(URLOPEN)
    def test_saving_a_video_fetches_its_poster_after_commit(self, mock_urlopen):
        _youtube_returns(mock_urlopen)
        with self.captureOnCommitCallbacks(execute=True):
            video = self._video()
        video.refresh_from_db()
        self.assertRegex(video.poster.name, OPAQUE_NAME)

    @override_settings(VIDEO_POSTER_AUTO_FETCH=True)
    @patch(URLOPEN, side_effect=socket.timeout('timed out'))
    def test_failed_download_does_not_break_saving_the_video(self, _urlopen):
        with self.assertLogs('catalog.posters', level='WARNING') as logs:
            with self.captureOnCommitCallbacks(execute=True):
                video = self._video()
        self.assertTrue(Video.objects.filter(pk=video.pk).exists())
        video.refresh_from_db()
        self.assertFalse(video.poster)
        self.assertIn('saved without a poster', logs.output[0])

    @override_settings(VIDEO_POSTER_AUTO_FETCH=True)
    @patch(URLOPEN)
    def test_a_garbage_response_does_not_break_saving_either(self, mock_urlopen):
        _youtube_returns(mock_urlopen, data=b'<html>not an image</html>')
        with self.assertLogs('catalog.posters', level='WARNING'):
            with self.captureOnCommitCallbacks(execute=True):
                video = self._video()
        video.refresh_from_db()
        self.assertFalse(video.poster)

    @override_settings(VIDEO_POSTER_AUTO_FETCH=True)
    @patch('catalog.posters.transaction.on_commit', side_effect=RuntimeError('boom'))
    @patch(URLOPEN)
    def test_failure_to_schedule_the_fetch_does_not_break_saving(self, mock_urlopen, _on_commit):
        with self.assertLogs('catalog.posters', level='ERROR'):
            video = self._video()
        self.assertTrue(Video.objects.filter(pk=video.pk).exists())
        mock_urlopen.assert_not_called()

    @override_settings(VIDEO_POSTER_AUTO_FETCH=True)
    @patch(URLOPEN)
    def test_existing_poster_is_never_overwritten_on_save(self, mock_urlopen):
        video = self._video()
        video.poster.save('subida-por-carla.png', ContentFile(_jpeg_bytes((10, 10, 10))), save=False)
        with self.captureOnCommitCallbacks(execute=True):
            video.title = 'Título nuevo'
            video.save()
        mock_urlopen.assert_not_called()

    @patch(URLOPEN)
    def test_tests_never_auto_fetch_by_default(self, mock_urlopen):
        with self.captureOnCommitCallbacks(execute=True):
            self._video()
        mock_urlopen.assert_not_called()
