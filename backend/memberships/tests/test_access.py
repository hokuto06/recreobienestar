from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from catalog.models import Category, Video
from memberships.models import MembershipPlan, Subscription
from memberships.services import can_access_video
from payments.models import OfferingPurchase, PurchaseStatus
from site_content.models import Offering

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

    # ── Phase 5A: grace period, end-to-end via can_access_video ────────
    def test_grace_period_keeps_video_accessible_past_ends_at_then_denies_after(self):
        video = self._video(access_level='plan1')
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=self.now - timedelta(hours=1),
            grace_ends_at=self.now + timedelta(days=3),
        )
        self.assertTrue(can_access_video(self.user, video))

        # Grace elapses too — access is lost, no code change needed to
        # observe it: is_expired() is evaluated live against `at`.
        self.assertFalse(can_access_video(self.user, video, at=self.now + timedelta(days=4)))
        # Same outcome once grace_ends_at itself is in the past.
        sub.grace_ends_at = self.now - timedelta(hours=1)
        sub.save(update_fields=['grace_ends_at'])
        self.assertFalse(can_access_video(self.user, video))

    def test_cancelled_retains_access_until_end_date(self):
        # Cancelling stops future renewal, but a member who already paid
        # for the current period keeps access until ends_at passes.
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='cancelled',
            ends_at=self.now + timedelta(days=10), cancelled_at=self.now,
        )
        video = self._video(access_level='plan1')
        self.assertTrue(can_access_video(self.user, video))

    def test_cancelled_denies_access_after_end_date(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='cancelled',
            ends_at=self.now - timedelta(days=1), cancelled_at=self.now - timedelta(days=2),
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

    def test_inactive_plan_denies_access(self):
        self.plan1.is_active = False
        self.plan1.save()
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=self.now + timedelta(days=10),
        )
        video = self._video(access_level='plan1')
        self.assertFalse(can_access_video(self.user, video))

    def test_staff_bypasses_everything_including_unpublished(self):
        staff_user = User.objects.create_user(username='staffer', password='x', is_staff=True)
        video = self._video(access_level='plan2', is_published=False)
        self.assertTrue(can_access_video(staff_user, video))


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

    # ── Phase 5A: grace period extends the access window ───────────────
    def test_grace_ends_at_in_future_grants_access_past_ends_at(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='active',
            ends_at=self.now - timedelta(days=1),
            grace_ends_at=self.now + timedelta(days=2),
        )
        self.assertTrue(sub.is_active())

    def test_both_ends_at_and_grace_ends_at_past_denies_access(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='active',
            ends_at=self.now - timedelta(days=3),
            grace_ends_at=self.now - timedelta(days=1),
        )
        self.assertFalse(sub.is_active())

    def test_grace_ends_at_earlier_than_ends_at_does_not_shorten_access(self):
        # A grace stamp earlier than ends_at (e.g. stale, or set by
        # mistake) must never shorten an otherwise-valid window — ends_at
        # alone governs, exactly as if grace_ends_at were unset.
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='active',
            ends_at=self.now + timedelta(days=5),
            grace_ends_at=self.now + timedelta(days=1),
        )
        self.assertTrue(sub.is_active())

    def test_past_due_with_future_grace_ends_at_denies_access(self):
        # Grace extends an otherwise-live subscription; it never
        # resurrects one already marked PAST_DUE.
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='past_due',
            ends_at=self.now - timedelta(days=1),
            grace_ends_at=self.now + timedelta(days=10),
        )
        self.assertFalse(sub.is_active())

    def test_expired_status_with_future_grace_ends_at_denies_access(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='expired',
            ends_at=self.now - timedelta(days=1),
            grace_ends_at=self.now + timedelta(days=10),
        )
        self.assertFalse(sub.is_active())

    def test_trial_grants_access_with_trial_ends_at_set_but_unused(self):
        # trial_ends_at is stored (Phase 5A) but is_active() doesn't
        # consult it yet — TRIAL keeps granting access exactly as before.
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='trial',
            ends_at=self.now + timedelta(days=7),
            trial_ends_at=self.now + timedelta(days=7),
        )
        self.assertTrue(sub.is_active())

    def test_start_grace_stamps_grace_ends_at_from_plan_grace_days(self):
        self.plan.grace_days = 5
        self.plan.save()
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='active',
            ends_at=self.now - timedelta(days=1),
        )
        sub.start_grace()
        self.assertIsNotNone(sub.grace_ends_at)
        self.assertAlmostEqual(
            sub.grace_ends_at, timezone.now() + timedelta(days=5), delta=timedelta(seconds=5),
        )
        # start_grace() does not save — matches OfferingPurchase's own
        # status-transition convention (caller controls save()).
        sub.refresh_from_db()
        self.assertIsNone(sub.grace_ends_at)

    def test_clear_grace_resets_grace_ends_at(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status='active',
            ends_at=self.now + timedelta(days=10),
            grace_ends_at=self.now + timedelta(days=2),
        )
        sub.clear_grace()
        self.assertIsNone(sub.grace_ends_at)


class OfferingPurchaseAccessTests(TestCase):
    """Phase 4A: can_access_video()'s offering-purchase path — an
    ADDITIONAL way to reach a video, OR'd alongside membership access (see
    memberships/services.py). These must never interfere with the
    membership-only behavior covered by AccessControlTests above; several
    tests here deliberately re-check that those earlier gates (unpublished,
    FREE) still win regardless of any purchase."""

    def setUp(self):
        self.now = timezone.now()
        self.category = Category.objects.create(name='Pilates')
        self.user = User.objects.create_user(username='compradora', password='x')
        self.plan1 = MembershipPlan.objects.create(tier='plan1', name='Plan 1', price=1000)

    def _video(self, **kwargs):
        defaults = dict(
            title='Video', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True,
        )
        defaults.update(kwargs)
        return Video.objects.create(**defaults)

    def _offering(self, *videos, **kwargs):
        defaults = dict(name='Curso', price=5000)
        defaults.update(kwargs)
        offering = Offering.objects.create(**defaults)
        if videos:
            offering.videos.set(videos)
        return offering

    def _purchase(self, offering, status=PurchaseStatus.COMPLETED, user=None):
        return OfferingPurchase.objects.create(
            user=user or self.user, offering=offering, status=status,
        )

    # ── a completed purchase grants access ──────────────────────────
    def test_completed_purchase_grants_access_to_bundled_video(self):
        video = self._video(access_level='plan1')
        offering = self._offering(video)
        self._purchase(offering, status=PurchaseStatus.COMPLETED)
        self.assertTrue(can_access_video(self.user, video))

    def test_pending_purchase_does_not_grant_access(self):
        video = self._video(access_level='plan1')
        offering = self._offering(video)
        self._purchase(offering, status=PurchaseStatus.PENDING)
        self.assertFalse(can_access_video(self.user, video))

    def test_failed_purchase_does_not_grant_access(self):
        video = self._video(access_level='plan1')
        offering = self._offering(video)
        self._purchase(offering, status=PurchaseStatus.FAILED)
        self.assertFalse(can_access_video(self.user, video))

    def test_completed_purchase_of_unrelated_offering_grants_no_access(self):
        video = self._video(access_level='plan1')
        other_video = self._video(title='Otro', access_level='plan1')
        offering = self._offering(other_video)  # bundles a DIFFERENT video
        self._purchase(offering, status=PurchaseStatus.COMPLETED)
        self.assertFalse(can_access_video(self.user, video))

    # ── overlap: membership and purchase are independent paths ──────
    def test_membership_only_grants_access_without_any_purchase(self):
        video = self._video(access_level='plan1')
        self._offering(video)  # exists, but never purchased
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=self.now + timedelta(days=10),
        )
        self.assertTrue(can_access_video(self.user, video))

    def test_purchase_only_grants_access_without_any_membership(self):
        video = self._video(access_level='plan1')
        offering = self._offering(video)
        self._purchase(offering, status=PurchaseStatus.COMPLETED)
        self.assertTrue(can_access_video(self.user, video))

    def test_membership_and_purchase_both_grant_access_independently(self):
        video = self._video(access_level='plan1')
        offering = self._offering(video)
        self._purchase(offering, status=PurchaseStatus.COMPLETED)
        Subscription.objects.create(
            user=self.user, plan=self.plan1, status='active',
            ends_at=self.now + timedelta(days=10),
        )
        self.assertTrue(can_access_video(self.user, video))

    # ── the offering path never overrides an earlier gate ───────────
    def test_offering_path_does_not_bypass_unpublished_gate(self):
        video = self._video(access_level='plan1', is_published=False)
        offering = self._offering(video)
        self._purchase(offering, status=PurchaseStatus.COMPLETED)
        self.assertFalse(can_access_video(self.user, video))

    def test_offering_path_does_not_affect_free_videos(self):
        video = self._video(access_level='free')
        # An unrelated completed purchase exists for this user, but it
        # doesn't bundle THIS video — free access must not depend on it,
        # and must still work for anonymous visitors with no purchase at
        # all.
        other_video = self._video(title='Otro', access_level='plan1')
        offering = self._offering(other_video)
        self._purchase(offering, status=PurchaseStatus.COMPLETED)
        self.assertTrue(can_access_video(self.user, video))
        self.assertTrue(can_access_video(None, video))

    # ── anonymous ────────────────────────────────────────────────────
    def test_anonymous_user_with_no_purchase_denied(self):
        video = self._video(access_level='plan1')
        self._offering(video)  # exists, but never purchased by anyone
        self.assertFalse(can_access_video(None, video))

    # ── prefetched `purchases` list matches the default live-query path ──
    def test_purchases_prefetch_list_grants_access_same_as_live_query(self):
        video = self._video(access_level='plan1')
        offering = self._offering(video)
        self._purchase(offering, status=PurchaseStatus.COMPLETED)
        purchases = list(
            OfferingPurchase.objects.filter(user=self.user)
            .select_related('offering').prefetch_related('offering__videos')
        )
        self.assertTrue(can_access_video(self.user, video, purchases=purchases))
