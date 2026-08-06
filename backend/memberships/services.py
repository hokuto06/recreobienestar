"""
Access-control domain logic.

Now wired into the public video library/detail views (accounts/catalog
apps) and the read-only API — this is the ONLY place that decides who can
watch what. Views, templates, and serializers must call can_access_video()
rather than re-deriving the rules.

Performance note: every function here accepts an optional `subscriptions`
list. Pass a pre-fetched `list(user.subscriptions.select_related('plan'))`
when checking access for MANY videos in one request (dashboard, video
library, API list) — without it, checking N videos means N separate
queries against the user's subscriptions, one per call. See
accounts/views.py:dashboard and catalog/public_views.py:video_library for
the batch-fetch call site; catalog/views.py:VideoViewSet does the same for
the API. A single video_detail check doesn't need this — one video means
one query either way.
"""
from common.choices import VideoAccessLevel


def _active_subscriptions_matching(user, subscriptions, at, predicate):
    """Shared iteration: either a pre-fetched in-memory list (no query) or
    a fresh queryset (one query), filtered down to subscriptions whose
    plan matches `predicate` and are currently active."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    candidates = (
        subscriptions if subscriptions is not None
        else user.subscriptions.select_related('plan').all()
    )
    for subscription in candidates:
        if predicate(subscription.plan) and subscription.is_active(at=at):
            return True
    return False


def user_has_active_plan(user, tier, at=None, subscriptions=None):
    """True if `user` has a currently-active (non-expired) subscription to
    the plan identified by `tier` (a common.choices.PlanTier value) AND
    that plan is still active. A deactivated plan (Carla turned it off in
    the admin) grants no access even to members with an otherwise-valid
    subscription to it — "inactive plans do not grant access"."""
    return _active_subscriptions_matching(
        user, subscriptions, at,
        predicate=lambda plan: plan.tier == tier and plan.is_active,
    )


def user_has_any_active_paid_plan(user, at=None, subscriptions=None):
    return _active_subscriptions_matching(
        user, subscriptions, at,
        predicate=lambda plan: plan.is_active,
    )


def can_access_video(user, video, at=None, subscriptions=None):
    """The single source of truth for "can this user watch this video right
    now". Mirrors the rules:
      - staff/superusers can access ANY video, published or not — this is
        the one exception, needed so Carla can preview draft/locked content
        without having to grant herself a paid subscription
      - unpublished videos are never accessible to anyone else
      - free videos are accessible to everyone, including anonymous users
      - plan1/plan2 videos require an active subscription to that exact plan
      - all_paid videos require an active subscription to any plan
      - an expired subscription grants no access, even if its status field
        hasn't caught up yet (see Subscription.is_expired)

    `subscriptions`: see module docstring — pass a pre-fetched list when
    checking many videos in one request to avoid N+1 queries.
    """
    if user is not None and getattr(user, 'is_authenticated', False) and (
        user.is_staff or user.is_superuser
    ):
        return True

    if not video.is_published:
        return False

    if video.access_level == VideoAccessLevel.FREE:
        return True

    if video.access_level == VideoAccessLevel.ALL_PAID:
        return user_has_any_active_paid_plan(user, at=at, subscriptions=subscriptions)

    # video.access_level is a specific plan tier (plan1/plan2).
    return user_has_active_plan(user, video.access_level, at=at, subscriptions=subscriptions)
