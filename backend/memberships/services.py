"""
Access-control domain logic.

Deliberately NOT wired to any public view yet — payments and public
auth/registration come in a later phase. This module exists so the rules
can be defined and unit-tested now, and reused unmodified once those
public-facing pieces land.
"""
from common.choices import VideoAccessLevel


def user_has_active_plan(user, tier, at=None):
    """True if `user` has a currently-active (non-expired) subscription to
    the plan identified by `tier` (a common.choices.PlanTier value)."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    for subscription in user.subscriptions.filter(plan__tier=tier):
        if subscription.is_active(at=at):
            return True
    return False


def user_has_any_active_paid_plan(user, at=None):
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    for subscription in user.subscriptions.select_related('plan'):
        if subscription.is_active(at=at):
            return True
    return False


def can_access_video(user, video, at=None):
    """The single source of truth for "can this user watch this video right
    now". Mirrors the rules:
      - unpublished videos are never accessible (regardless of plan)
      - free videos are accessible to everyone, including anonymous users
      - plan1/plan2 videos require an active subscription to that exact plan
      - all_paid videos require an active subscription to any plan
      - an expired subscription grants no access, even if its status field
        hasn't caught up yet (see Subscription.is_expired)
    """
    if not video.is_published:
        return False

    if video.access_level == VideoAccessLevel.FREE:
        return True

    if video.access_level == VideoAccessLevel.ALL_PAID:
        return user_has_any_active_paid_plan(user, at=at)

    # video.access_level is a specific plan tier (plan1/plan2).
    return user_has_active_plan(user, video.access_level, at=at)
