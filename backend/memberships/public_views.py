"""
Phase 5B-1: the Django-rendered page that lets a logged-in member start
the free 7-day trial. Mirrors site_content.public_views.offering_detail's
shape (4B-3) — same CSRF/session problem, same solution: a Django page
gives the "Empezar prueba" button a real {% csrf_token %} and a session
to POST against, since the static home (served by nginx, not Django) has
neither.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from common.choices import PlanTier

from .models import MembershipPlan, Subscription
from .services import user_has_any_active_paid_plan


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
