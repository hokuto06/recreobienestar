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


class DashboardMembershipStatusTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.category = Category.objects.create(name='Yoga')
        self.plan = MembershipPlan.objects.create(tier='plan1', name='Plan Esencial', price=1000)

    def test_dashboard_shows_free_plan_when_no_subscription(self):
        user = User.objects.create_user(username='gratis', password='x')
        self.client.force_login(user)
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertContains(resp, 'Plan gratuito')

    def test_dashboard_shows_active_status(self):
        user = User.objects.create_user(username='activa', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan, status='active', ends_at=self.now + timedelta(days=20),
        )
        self.client.force_login(user)
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertContains(resp, 'Activa')
        self.assertContains(resp, 'Plan Esencial')

    def test_dashboard_shows_expired_status_and_cta(self):
        user = User.objects.create_user(username='vencida', password='x')
        Subscription.objects.create(
            user=user, plan=self.plan, status='active', ends_at=self.now - timedelta(days=1),
        )
        self.client.force_login(user)
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertContains(resp, 'Venció el')
        self.assertContains(resp, 'Renovar membresía')

    def test_dashboard_lists_available_and_locked_videos(self):
        user = User.objects.create_user(username='mixta', password='x')
        Video.objects.create(
            title='Gratis', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True, access_level='free',
        )
        Video.objects.create(
            title='Pago', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True, access_level='plan1',
        )
        self.client.force_login(user)
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertContains(resp, 'Gratis')
        self.assertContains(resp, 'Pago')  # shown, but in the locked section
        self.assertContains(resp, 'Contenido bloqueado')


class DashboardQueryCountTests(TestCase):
    """Regression guard for the N+1 fixed in this audit: checking access
    for every video on the dashboard must not issue one subscriptions
    query per video."""

    def setUp(self):
        self.now = timezone.now()
        self.category = Category.objects.create(name='Yoga')
        self.plan = MembershipPlan.objects.create(tier='plan1', name='Plan Esencial', price=1000)
        self.user = User.objects.create_user(username='queryuser', password='x')
        Subscription.objects.create(
            user=self.user, plan=self.plan, status='active', ends_at=self.now + timedelta(days=20),
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
            resp = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_dashboard_query_count_does_not_scale_with_video_count(self):
        small = self._query_count_for_n_videos(3)
        large = self._query_count_for_n_videos(20)
        self.assertEqual(
            small, large,
            f'Query count should not grow with video count (N+1?): {small} queries for '
            f'3 videos vs {large} for 20.',
        )
