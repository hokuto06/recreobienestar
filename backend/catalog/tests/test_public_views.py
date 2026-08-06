from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Video
from memberships.models import MembershipPlan, Subscription

User = get_user_model()


class VideoDetailAccessTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.category = Category.objects.create(name='Pilates')
        self.plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)

        self.free_video = Video.objects.create(
            title='Video libre', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True, access_level='free',
        )
        self.paid_video = Video.objects.create(
            title='Video exclusivo', youtube_url='https://youtu.be/jNQXAC9IVRw',
            category=self.category, is_published=True, access_level='plan1',
        )
        self.unpublished_video = Video.objects.create(
            title='Sin publicar', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=False, access_level='free',
        )

    def _detail_url(self, video):
        return reverse('catalog:video_detail', args=[video.slug])

    # ── free access ──────────────────────────────────────────────────
    def test_anonymous_can_view_free_video(self):
        resp = self.client.get(self._detail_url(self.free_video))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.free_video.youtube_video_id)

    def test_free_user_denied_paid_video(self):
        user = User.objects.create_user(username='gratis', password='x')
        self.client.force_login(user)
        resp = self.client.get(self._detail_url(self.paid_video))
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, 'catalog/video_locked.html')

    # ── active / expired ─────────────────────────────────────────────
    def test_active_plan_grants_access(self):
        user = User.objects.create_user(username='activa', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan1, status='active', ends_at=self.now + timedelta(days=10),
        )
        self.client.force_login(user)
        resp = self.client.get(self._detail_url(self.paid_video))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.paid_video.youtube_video_id)

    def test_expired_membership_denied_immediately(self):
        user = User.objects.create_user(username='vencida', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan1, status='active', ends_at=self.now - timedelta(minutes=1),
        )
        self.client.force_login(user)
        resp = self.client.get(self._detail_url(self.paid_video))
        self.assertEqual(resp.status_code, 403)

    def test_cancelled_before_end_date_still_has_access(self):
        user = User.objects.create_user(username='cancelada', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan1, status='cancelled', ends_at=self.now + timedelta(days=5),
        )
        self.client.force_login(user)
        resp = self.client.get(self._detail_url(self.paid_video))
        self.assertEqual(resp.status_code, 200)

    def test_inactive_plan_denies_access_even_with_subscription(self):
        self.plan1.is_active = False
        self.plan1.save()
        user = User.objects.create_user(username='planinactivo', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan1, status='active', ends_at=self.now + timedelta(days=10),
        )
        self.client.force_login(user)
        resp = self.client.get(self._detail_url(self.paid_video))
        # Deactivating a plan (Carla turned it off in the admin) doesn't
        # retroactively touch the subscription row itself (still "active"
        # by status/dates — see memberships/tests/test_models.py), but it
        # must still deny access: "inactive plans do not grant access".
        self.assertEqual(resp.status_code, 403)

    # ── unpublished ──────────────────────────────────────────────────
    def test_unpublished_video_denied_for_regular_user(self):
        resp = self.client.get(self._detail_url(self.unpublished_video))
        self.assertEqual(resp.status_code, 403)

    def test_unpublished_video_accessible_to_staff(self):
        staff = User.objects.create_user(username='staffuser', password='x', is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(self._detail_url(self.unpublished_video))
        self.assertEqual(resp.status_code, 200)

    def test_staff_can_access_locked_paid_video_without_subscription(self):
        staff = User.objects.create_user(username='staffuser2', password='x', is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(self._detail_url(self.paid_video))
        self.assertEqual(resp.status_code, 200)

    # ── locked page must not leak the YouTube ID ────────────────────
    def test_locked_page_does_not_leak_youtube_id(self):
        resp = self.client.get(self._detail_url(self.paid_video))
        self.assertEqual(resp.status_code, 403)
        content = resp.content.decode()
        self.assertNotIn(self.paid_video.youtube_video_id, content)
        self.assertNotIn('youtube', content.lower())
        self.assertNotIn('youtube-nocookie', content)

    def test_video_detail_404_for_nonexistent_slug(self):
        resp = self.client.get(reverse('catalog:video_detail', args=['no-existe']))
        self.assertEqual(resp.status_code, 404)

    def test_404_uses_branded_template_not_django_default(self):
        with self.settings(DEBUG=False):
            resp = self.client.get('/esta-pagina-no-existe/')
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, 'Recreo Bienestar', status_code=404)
        self.assertNotContains(resp, 'not found on this server', status_code=404)


class VideoLibraryTests(TestCase):
    def setUp(self):
        self.cat_a = Category.objects.create(name='Categoría A')
        self.cat_b = Category.objects.create(name='Categoría B')
        for i in range(3):
            Video.objects.create(
                title=f'Video A{i}', youtube_url='https://youtu.be/dQw4w9WgXcQ',
                category=self.cat_a, is_published=True, access_level='free',
            )
        Video.objects.create(
            title='Video B0', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.cat_b, is_published=True, access_level='free',
        )
        Video.objects.create(
            title='Video oculto', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.cat_a, is_published=False, access_level='free',
        )

    def test_library_lists_only_published_videos(self):
        resp = self.client.get(reverse('catalog:video_library'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Video A0')
        self.assertNotContains(resp, 'Video oculto')

    def test_filter_by_category(self):
        resp = self.client.get(reverse('catalog:video_library'), {'category': self.cat_b.slug})
        self.assertContains(resp, 'Video B0')
        self.assertNotContains(resp, 'Video A0')

    def test_empty_state_when_filter_matches_nothing(self):
        empty_cat = Category.objects.create(name='Vacía')
        resp = self.client.get(reverse('catalog:video_library'), {'category': empty_cat.slug})
        self.assertContains(resp, 'No encontramos videos')


class VideoCardThumbnailLeakTests(TestCase):
    """The library grid renders locked videos too (with a lock badge), so
    the card partial itself must not leak the YouTube ID via a thumbnail
    background-image the way video_locked.html already took care not to."""

    def setUp(self):
        self.category = Category.objects.create(name='Pilates')
        self.plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        self.locked_video = Video.objects.create(
            title='Clase exclusiva', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True, access_level='plan1',
        )

    def test_library_card_does_not_leak_thumbnail_for_locked_video(self):
        resp = self.client.get(reverse('catalog:video_library'))
        self.assertContains(resp, 'Clase exclusiva')
        content = resp.content.decode()
        self.assertNotIn(self.locked_video.youtube_video_id, content)
        self.assertNotIn('img.youtube.com', content)

    def test_dashboard_card_does_not_leak_thumbnail_for_locked_video(self):
        user = User.objects.create_user(username='sindashboard', password='x')
        self.client.force_login(user)
        resp = self.client.get(reverse('accounts:dashboard'))
        content = resp.content.decode()
        self.assertNotIn(self.locked_video.youtube_video_id, content)
        self.assertNotIn('img.youtube.com', content)


class VideoLibraryQueryCountTests(TestCase):
    """Same N+1 regression guard as the dashboard, for /videoteca/."""

    def setUp(self):
        self.category = Category.objects.create(name='Pilates')
        self.plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        self.user = User.objects.create_user(username='queryuser2', password='x')
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=timezone.now() + timedelta(days=10),
        )
        self.client.force_login(self.user)

    def _query_count_for_n_videos(self, n):
        Video.objects.all().delete()
        for i in range(n):
            Video.objects.create(
                title=f'Video {i}', youtube_url='https://youtu.be/dQw4w9WgXcQ',
                category=self.category, is_published=True,
                access_level='plan1' if i % 2 == 0 else 'free',
            )
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('catalog:video_library'))
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_library_query_count_does_not_scale_with_video_count(self):
        # Both counts stay within one page (PAGE_SIZE=12) so pagination
        # itself isn't the variable being measured — only per-video access
        # checks are.
        small = self._query_count_for_n_videos(3)
        large = self._query_count_for_n_videos(10)
        self.assertEqual(
            small, large,
            f'Query count should not grow with video count (N+1?): {small} vs {large}.',
        )
