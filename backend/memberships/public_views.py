"""
Phase 5B-1: the Django-rendered page that lets a logged-in member start
the free 7-day trial. Mirrors site_content.public_views.offering_detail's
shape (4B-3) — same CSRF/session problem, same solution: a Django page
gives the "Empezar prueba" button a real {% csrf_token %} and a session
to POST against, since the static home (served by nginx, not Django) has
neither.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from common.choices import PlanTier

from .models import MembershipPlan, Subscription
from .services import (
    billing_cadence_for_plan, user_has_active_paid_subscription, user_has_active_trial,
    user_has_any_active_paid_plan,
)
from .views import resolve_purchasable_plan


@login_required
def prueba_gratis(request):
    """GET /prueba-gratis/ — explains the trial and shows the "Empezar
    prueba" button, UNLESS the visitor is ineligible (already used their
    trial, or already has an active paid subscription) — in which case a
    clear message is shown instead of a button that would just fail (see
    memberships.views.StartTrialView, which enforces the same two
    rejections again server-side — this page's eligibility check is for a
    better UX, not itself the security boundary).

    @login_required rather than a bare public view: same reasoning as
    site_content.public_views.offering_detail — a trial must be
    attributed to a real user, so there's no reason to show this page to
    an anonymous visitor. Redirects to /ingresar/?next=... for free.
    """
    plan = MembershipPlan.objects.filter(tier=PlanTier.PLAN1, is_active=True).first()
    already_used_trial = Subscription.objects.filter(user=request.user, is_trial=True).exists()
    has_active_paid_plan = user_has_any_active_paid_plan(request.user)

    context = {
        'plan': plan,
        'eligible': plan is not None and not already_used_trial and not has_active_paid_plan,
        'already_used_trial': already_used_trial,
        'has_active_paid_plan': has_active_paid_plan,
    }
    return render(request, 'memberships/prueba_gratis.html', context)


_CADENCE_LABELS = {
    (1, 'months'): 'por mes',
    (12, 'months'): 'por año',
}


@login_required
def membresia_detail(request, slug):
    """GET /membresia/<slug>/ — a paid plan's own page (Phase 5B-2a):
    plan, price and billing cadence, plus the "Suscribirme" button that
    POSTs to /api/subscribe/ (memberships.views.StartSubscriptionView) via
    site.js's fetch/X-CSRFToken pattern and then redirects to Mercado
    Pago. Mirrors site_content.public_views.offering_detail (4B-3):
    login-required for the same reason (a subscription must belong to a
    real user), and 404 for an unknown/inactive plan — and for the
    free-trial plan too, which has its own page (/prueba-gratis/).

    Eligibility here is UX only; StartSubscriptionView enforces the same
    rejections again server-side.
    """
    plan = resolve_purchasable_plan(slug)
    if plan is None or plan.tier == PlanTier.PLAN1:
        raise Http404('Plan no encontrado')

    cadence = billing_cadence_for_plan(plan)
    has_active_paid_subscription = user_has_active_paid_subscription(request.user)
    context = {
        'plan': plan,
        'cadence_label': _CADENCE_LABELS.get(cadence, ''),
        'available': cadence is not None,
        'has_active_paid_subscription': has_active_paid_subscription,
        'has_active_trial': user_has_active_trial(request.user),
        'eligible': cadence is not None and not has_active_paid_subscription,
    }
    return render(request, 'memberships/membresia_detail.html', context)


@login_required
def membresia_estado(request):
    """GET /membresia/estado/ — the preapproval's back_url: where Mercado
    Pago sends the member after authorizing (or abandoning) the recurring
    charge. Purely informational: it reads nothing from the query string,
    calls nothing, and changes nothing — confirming the subscription is
    the subscription webhook's job (Phase 5B-2b). It only shows the
    member's most recent paid-plan subscription as it currently stands.
    """
    subscription = (
        Subscription.objects.filter(user=request.user, is_trial=False)
        .select_related('plan').order_by('-created_at').first()
    )
    return render(request, 'memberships/membresia_estado.html', {
        'subscription': subscription,
        'is_active': subscription is not None and subscription.is_active(),
    })
