from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from catalog.models import Category, Video
from memberships.models import MembershipPlan, Subscription
from memberships.services import can_access_video

User = get_user_model()


class AccessControlTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.category = Category.objects.create(name='Pilates')
        self.user = User.objects.create_user(username='carla_member', password='x')
        self.plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)
        self.plan2 = MembershipPlan.objects.create(tier='plan2', name='Plan 2', price=2000)

    def _video(self, **kwargs):
        defaults = dict(
            title='Video', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True,
        )
        defaults.update(kwargs)
        return Video.objects.create(**defaults)

    # ── free videos ──────────────────────────────────────────────────
    def test_free_video_accessible_to_anonymous(self):
        video = self._video(access_level='free')
        self.assertTrue(can_access_video(None, video))

    def test_free_video_accessible_without_subscription(self):
        video = self._video(access_level='free')
        self.assertTrue(can_access_video(self.user, video))

    # ── unpublished ──────────────────────────────────────────────────
    def test_unpublished_video_denied_even_if_free(self):
        video = self._video(access_level='free', is_published=False)
        self.assertFalse(can_access_video(self.user, video))

    def test_unpublished_video_denied_with_active_membership(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=self.now + timedelta(days=10),
        )
        video = self._video(access_level='plan1', is_published=False)
        self.assertFalse(can_access_video(self.user, video))

    # ── active membership grants access ─────────────────────────────
    def test_active_membership_grants_access_to_matching_plan_video(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=self.now + timedelta(days=10),
        )
        video = self._video(access_level='plan1')
        self.assertTrue(can_access_video(self.user, video))

    def test_active_plan1_does_not_grant_access_to_plan2_video(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=self.now + timedelta(days=10),
        )
        video = self._video(access_level='plan2')
        self.assertFalse(can_access_video(self.user, video))

    def test_active_any_plan_grants_access_to_all_paid_video(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan2, status='active',
            ends_at=self.now + timedelta(days=10),
        )
        video = self._video(access_level='all_paid')
        self.assertTrue(can_access_video(self.user, video))

    def test_trial_status_grants_access(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='trial',
            ends_at=self.now + timedelta(days=3),
        )
        video = self._video(access_level='plan1')
        self.assertTrue(can_access_video(self.user, video))

    # ── expired membership denies access ────────────────────────────
    def test_expired_membership_denies_access_immediately(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=self.now - timedelta(seconds=1),
        )
        video = self._video(access_level='plan1')
        self.assertFalse(can_access_video(self.user, video))

    def test_expired_status_field_denies_access(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='expired',
            ends_at=self.now + timedelta(days=10),  # status lies; still denied
        )
        video = self._video(access_level='plan1')
        self.assertFalse(can_access_video(self.user, video))

    def test_past_due_denies_access(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='past_due',
            ends_at=self.now + timedelta(days=10),
        )
        video = self._video(access_level='plan1')
        self.assertFalse(can_access_video(self.user, video))

    def test_cancelled_denies_access(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='cancelled',
            ends_at=self.now + timedelta(days=10), cancelled_at=self.now,
        )
        video = self._video(access_level='plan1')
        self.assertFalse(can_access_video(self.user, video))

    def test_no_end_date_treated_as_not_expired(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active', ends_at=None,
        )
        video = self._video(access_level='plan1')
        self.assertTrue(can_access_video(self.user, video))

    def test_anonymous_user_denied_paid_video(self):
        video = self._video(access_level='plan1')
        self.assertFalse(can_access_video(None, video))


class SubscriptionModelTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = User.objects.create_user(username='bea', password='x')
        self.plan = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)

    def test_is_expired_true_after_end_date(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='active',
            ends_at=self.now - timedelta(days=1),
        )
        self.assertTrue(sub.is_expired())

    def test_is_expired_false_before_end_date(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='active',
            ends_at=self.now + timedelta(days=1),
        )
        self.assertFalse(sub.is_expired())

    def test_is_active_false_for_expired_even_if_status_active(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='active',
            ends_at=self.now - timedelta(minutes=1),
        )
        self.assertFalse(sub.is_active())
