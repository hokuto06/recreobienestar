from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, VideoProgress
from catalog.services import get_continue_watching, get_progress_map, record_video_view
from memberships.models import MembershipPlan

from .factories import make_video

User = get_user_model()


class RecordVideoViewTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Yoga')
        self.user = User.objects.create_user(username='u1', password='x')
        self.video = make_video(self.category, title='Sesión', access_level='free')

    def test_first_view_creates_progress_row(self):
        progress = record_video_view(self.user, self.video)
        self.assertIsNotNone(progress)
        self.assertEqual(VideoProgress.objects.count(), 1)
        self.assertFalse(progress.completed)

    def test_second_view_updates_last_viewed_without_duplicating(self):
        first = record_video_view(self.user, self.video)
        second = record_video_view(self.user, self.video)
        self.assertEqual(VideoProgress.objects.count(), 1)
        self.assertEqual(first.pk, second.pk)

    def test_anonymous_user_is_not_tracked(self):
        from django.contrib.auth.models import AnonymousUser
        result = record_video_view(AnonymousUser(), self.video)
        self.assertIsNone(result)
        self.assertEqual(VideoProgress.objects.count(), 0)

    def test_visiting_detail_page_records_progress(self):
        self.client.force_login(self.user)
        url = reverse('catalog:video_detail', args=[self.video.slug])
        self.client.get(url)
        self.assertTrue(VideoProgress.objects.filter(user=self.user, video=self.video).exists())

    def test_locked_video_detail_does_not_record_progress(self):
        plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        locked_video = make_video(self.category, title='Exclusivo', access_level='plan1')
        self.client.force_login(self.user)
        self.client.get(reverse('catalog:video_detail', args=[locked_video.slug]))
        self.assertFalse(VideoProgress.objects.filter(user=self.user, video=locked_video).exists())


class MarkVideoCompletedTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Yoga')
        self.user = User.objects.create_user(username='u2', password='x')
        self.video = make_video(self.category, title='Sesión', access_level='free')
        self.url = reverse('catalog:mark_video_completed', args=[self.video.slug])

    def test_requires_login(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/ingresar/', resp.url)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_marks_completed_and_redirects(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse('catalog:video_detail', args=[self.video.slug]))
        progress = VideoProgress.objects.get(user=self.user, video=self.video)
        self.assertTrue(progress.completed)
        self.assertIsNotNone(progress.completed_at)
        self.assertEqual(progress.progress_percent, 100)

    def test_completed_state_shown_on_detail_page(self):
        self.client.force_login(self.user)
        self.client.post(self.url)
        resp = self.client.get(reverse('catalog:video_detail', args=[self.video.slug]))
        self.assertContains(resp, 'Completado')

    def test_cannot_mark_locked_video_completed(self):
        plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        locked_video = make_video(self.category, title='Exclusivo', access_level='plan1')
        self.client.force_login(self.user)
        resp = self.client.post(reverse('catalog:mark_video_completed', args=[locked_video.slug]))
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(VideoProgress.objects.filter(user=self.user, video=locked_video).exists())


class ContinueWatchingTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Yoga')
        self.user = User.objects.create_user(username='u3', password='x')
        self.started = make_video(self.category, title='Empezado', access_level='free')
        self.completed = make_video(self.category, title='Terminado', access_level='free')
        record_video_view(self.user, self.started)
        record_video_view(self.user, self.completed)
        VideoProgress.objects.filter(user=self.user, video=self.completed).first().mark_completed()

    def test_continue_watching_excludes_completed(self):
        result = get_continue_watching(self.user)
        videos = [p.video for p in result]
        self.assertIn(self.started, videos)
        self.assertNotIn(self.completed, videos)

    def test_continue_watching_shown_on_dashboard(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertContains(resp, 'Continuar viendo')
        self.assertContains(resp, 'Empezado')

    def test_continue_watching_reflects_revoked_access(self):
        """A video started while the member had access must show as locked
        (and not leak its thumbnail) if access lapses before it's finished."""
        plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        # Distinct YouTube URL — self.started/self.completed share the
        # factory default and are legitimately unlocked on this same
        # dashboard page, so their thumbnails must not accidentally
        # satisfy this leak check.
        paid_video = make_video(
            self.category, title='Solo con plan', access_level='plan1',
            youtube_url='https://youtu.be/jNQXAC9IVRw',
        )
        # Simulate having watched it while subscribed (progress rows don't
        # depend on a subscription existing — they're independent of access).
        record_video_view(self.user, paid_video)
        self.client.force_login(self.user)
        resp = self.client.get(reverse('accounts:dashboard'))
        content = resp.content.decode()
        # Other, legitimately-unlocked videos on this same dashboard DO
        # show a real img.youtube.com thumbnail — the point of this test
        # is that THIS video's own id/thumbnail isn't among them.
        self.assertNotIn(paid_video.youtube_video_id, content)
        self.assertNotIn(f'img.youtube.com/vi/{paid_video.youtube_video_id}', content)


class ProgressMapTests(TestCase):
    def test_empty_for_anonymous(self):
        category = Category.objects.create(name='Yoga')
        video = make_video(category, title='V', access_level='free')
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(get_progress_map(AnonymousUser(), videos=[video]), {})
