"""
Access-control domain logic.

Now wired into the public video library/detail views (accounts/catalog
apps) and the read-only API — this is the ONLY place that decides who can
watch what. Views, templates, and serializers must call can_access_video()
rather than re-deriving the rules.

Performance note: every function here accepts an optional `subscriptions`
list (and, since Phase 4A, an optional `purchases` list — see
can_access_video below). Pass a pre-fetched
`list(user.subscriptions.select_related('plan'))` when checking access for
MANY videos in one request (dashboard, video library, API list) —
without it, checking N videos means N separate queries against the user's
subscriptions, one per call. See accounts/views.py:dashboard and
catalog/public_views.py:video_library for the batch-fetch call site;
catalog/views.py:VideoViewSet does the same for the API. A single
video_detail check doesn't need this — one video means one query either
way.
"""
import calendar
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from common.choices import SubscriptionStatus, VideoAccessLevel
from payments.models import OfferingPurchase, PurchaseStatus


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


def user_has_active_trial(user, at=None, subscriptions=None):
    """Phase 5B-1: True if `user` has a currently-active (is_active())
    Subscription in TRIAL status, on a currently-active plan — regardless
    of WHICH plan. A trial is a taste of EVERYTHING ("Probá todos los
    planes de entrenamiento"): full paid-catalog access while it lasts,
    unlike user_has_active_plan (tier-specific, and what governs a
    subscription once it's ACTIVE rather than TRIAL).

    Deliberately a standalone loop rather than routed through
    _active_subscriptions_matching: that helper's predicate only sees
    `plan`, not `subscription`, and this needs to filter on
    `subscription.status` too — not worth widening a helper two other,
    already-tested callers depend on for one extra branch.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    candidates = (
        subscriptions if subscriptions is not None
        else user.subscriptions.select_related('plan').all()
    )
    for subscription in candidates:
        if (
            subscription.status == SubscriptionStatus.TRIAL
            and subscription.plan.is_active
            and subscription.is_active(at=at)
        ):
            return True
    return False


def user_has_expired_trial(user):
    """Phase 5B-1: True if `user` has ever started a trial (a Subscription
    with status=TRIAL) whose access window has since elapsed. NOT an
    access decision (that's can_access_video/user_has_active_trial above)
    — used only for locked-content messaging (catalog.public_views.
    video_detail) to distinguish "your trial finished" from "you never
    had access"."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return any(
        sub.is_expired() for sub in user.subscriptions.filter(status=SubscriptionStatus.TRIAL)
    )


def user_has_purchased_offering_unlocking(user, video, purchases=None):
    """Phase 4A: True if `user` has a COMPLETED purchase
    (payments.OfferingPurchase) of an Offering whose `videos` M2M includes
    `video`. This is the offering-purchase counterpart to
    user_has_active_plan/user_has_any_active_paid_plan above — an
    independent, additional way to reach a video, OR'd in by
    can_access_video, never a replacement for the membership checks.

    `purchases`: mirrors the `subscriptions` prefetch pattern — pass a
    pre-fetched
    `list(OfferingPurchase.objects.filter(user=user)
        .select_related('offering').prefetch_related('offering__videos'))`
    when checking many videos in one request, to avoid one query per
    video. Deliberately NOT pre-filtered to COMPLETED at the call site
    (same reasoning as `subscriptions` not being pre-filtered to active):
    filtering happens here, in one place, exactly like
    Subscription.is_active() is what filters `subscriptions`.

    Accepts no `at` — a completed purchase doesn't expire the way a
    subscription does; there's nothing time-based to evaluate here.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if purchases is None:
        return OfferingPurchase.objects.unlocking(user, video).exists()
    for purchase in purchases:
        if purchase.status != PurchaseStatus.COMPLETED:
            continue
        if any(v.id == video.id for v in purchase.offering.videos.all()):
            return True
    return False


def get_current_subscription(user, subscriptions=None):
    """The subscription to treat as "your membership" for display purposes
    (dashboard, profile) — the most recently created one, active or not, so
    an expired/cancelled plan still shows as "your plan, expired" rather
    than silently falling back to "no plan". Not an access decision (see
    can_access_video for that) — purely what to show in the UI.

    Pass a pre-fetched `subscriptions` list to avoid a second query when the
    caller already fetched them (e.g. for can_access_video batching)."""
    candidates = (
        subscriptions if subscriptions is not None
        else list(user.subscriptions.select_related('plan').all())
        if user is not None and getattr(user, 'is_authenticated', False)
        else []
    )
    return max(candidates, key=lambda s: s.created_at, default=None)


def can_access_video(user, video, at=None, subscriptions=None, purchases=None):
    """The single source of truth for "can this user watch this video right
    now". Mirrors the rules:
      - staff/superusers can access ANY video, published or not — this is
        the one exception, needed so Carla can preview draft/locked content
        without having to grant herself a paid subscription
      - unpublished videos are never accessible to anyone else
      - free videos are accessible to everyone, including anonymous users
      - plan1/plan2/plan3 videos require an active subscription to that
        exact plan, OR (Phase 4A) a completed purchase of an Offering that
        bundles this video
      - all_paid videos require an active subscription to any plan, OR
        (Phase 4A) a completed offering purchase, same as above
      - an expired subscription grants no access, even if its status field
        hasn't caught up yet (see Subscription.is_expired)
      - Phase 5B-1: a currently-active TRIAL-status subscription grants
        access to ANY paid video, regardless of the video's own tier — a
        taste of everything, not just the trialed plan's tier (see
        user_has_active_trial). Checked right after the FREE branch, so
        it can only ever ADD access for a non-free video; it never runs
        for (and can never override) the staff, unpublished, or FREE
        branches above.

    Phase 4A note: membership and offering-purchase are two INDEPENDENT,
    additive access paths — OR'd together, never replacing one another.
    Overlap is expected and fine: a video may be reachable via both a
    membership AND a purchased offering at once. Neither path knows about
    the other; this function is the only place they're combined. The
    offering-purchase path is deliberately checked LAST and can only ever
    ADD access for a plan-gated video — it never runs for (and can never
    override) the staff, unpublished, or FREE branches above, which still
    return unconditionally exactly as before.

    `subscriptions` / `purchases`: see module docstring — pass pre-fetched
    lists when checking many videos in one request to avoid N+1 queries.
    """
    if user is not None and getattr(user, 'is_authenticated', False) and (
        user.is_staff or user.is_superuser
    ):
        return True

    if not video.is_published:
        return False

    if video.access_level == VideoAccessLevel.FREE:
        return True

    if user_has_active_trial(user, at=at, subscriptions=subscriptions):
        return True

    if video.access_level == VideoAccessLevel.ALL_PAID:
        if user_has_any_active_paid_plan(user, at=at, subscriptions=subscriptions):
            return True
    # video.access_level is a specific plan tier (plan1/plan2/plan3).
    elif user_has_active_plan(user, video.access_level, at=at, subscriptions=subscriptions):
        return True

    return user_has_purchased_offering_unlocking(user, video, purchases=purchases)


def user_has_active_paid_subscription(user, at=None, subscriptions=None):
    """Phase 5B-2a: True if `user` has a currently-active (is_active())
    subscription that is NOT the free trial — i.e. something they pay
    for (or that Carla granted by hand). Used only to block a second
    paid signup (no plan switching yet — see StartSubscriptionView).

    Deliberately NOT user_has_any_active_paid_plan: that one counts ANY
    active subscription on an active plan, including a TRIAL-status one,
    so it would wrongly block exactly the "trial member subscribes"
    path this phase exists for. A still-PENDING paid signup doesn't count
    either (is_active() is False for PENDING — not in ENTITLED_STATUSES).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    candidates = (
        subscriptions if subscriptions is not None
        else user.subscriptions.select_related('plan').all()
    )
    return any(
        not subscription.is_trial
        and subscription.status != SubscriptionStatus.TRIAL
        and subscription.is_active(at=at)
        for subscription in candidates
    )


# Phase 5B-2a: Mercado Pago preapproval billing cadence, derived from
# MembershipPlan.duration_days. MP's auto_recurring only accepts
# frequency_type 'days' or 'months' (no 'years'), so yearly is 12 months.
# Deliberately an explicit whitelist rather than arithmetic on
# duration_days: a plan with any other value (unset, 31, 90, ...) is not
# billable through this flow until someone decides what cadence it means
# — billing_cadence_for_plan() returns None and the signup is refused,
# never guessed.
_BILLING_CADENCE_BY_DURATION_DAYS = {
    30: (1, 'months'),
    365: (12, 'months'),
}


def billing_cadence_for_plan(plan):
    """(frequency, frequency_type) for `plan`'s MP preapproval, or None if
    its duration_days doesn't map to a known cadence."""
    return _BILLING_CADENCE_BY_DURATION_DAYS.get(plan.duration_days)


def supersede_active_trial(user, paid_subscription, at=None):
    """Phase 5B-2a (for 5B-2b to call): ends `user`'s currently-active
    free trial, if any, because `paid_subscription` has just been
    CONFIRMED by Mercado Pago. Deliberately NOT called at signup
    (StartSubscriptionView) — a member who abandons MP's page must keep
    their trial. Not wired to anything yet.

    Locks the user's trial rows (select_for_update) so a concurrent
    duplicate confirmation can't double-apply. Only a trial that's still
    active at `at` is touched; an already-expired or already-superseded
    one is left alone, so calling this twice is a no-op the second time.
    Returns the superseded trial Subscription, or None.
    """
    moment = at or timezone.now()
    with transaction.atomic():
        trials = user.subscriptions.select_for_update().select_related('plan').filter(is_trial=True)
        for trial in trials:
            if trial.superseded_by_id is None and trial.is_active(at=moment):
                trial.supersede_trial(paid_subscription, at=moment)
                trial.save(update_fields=['ends_at', 'superseded_by', 'updated_at'])
                return trial
    return None


def _add_months(moment, months):
    """`moment` + `months` calendar months, clamping the day to the target
    month's length (Jan 31 + 1 month = Feb 28/29), time and tzinfo kept."""
    month_index = moment.month - 1 + months
    year, month = moment.year + month_index // 12, month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def paid_period_end(plan, start):
    """Phase 5B-2b: when a confirmed charge's paid period ends — the same
    cadence the preapproval bills on (billing_cadence_for_plan): 30 days
    -> start + 1 calendar month, 365 days -> start + 12 calendar months.
    Fallback for a plan whose duration_days no longer maps to a cadence
    (edited after signup): start + duration_days, or 30 days if unset —
    MP already took the money, so access must still be granted."""
    cadence = billing_cadence_for_plan(plan)
    if cadence is not None:
        frequency, frequency_type = cadence
        if frequency_type == 'months':
            return _add_months(start, frequency)
        return start + timedelta(days=frequency)
    return start + timedelta(days=plan.duration_days or 30)
