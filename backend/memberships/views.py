from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.choices import PlanTier, SubscriptionStatus

from .models import MembershipPlan, Subscription
from .serializers import MembershipPlanSerializer
from .services import user_has_any_active_paid_plan


class MembershipPlanListView(generics.ListAPIView):
    """GET /api/plans/ — active plans only."""
    serializer_class = MembershipPlanSerializer
    queryset = MembershipPlan.objects.filter(is_active=True).order_by('display_order', 'name')


class StartTrialView(APIView):
    """POST /api/trial/ — starts the free 7-day trial (Phase 5B-1): no
    card, no payment, no Mercado Pago involvement whatsoever — just a
    Subscription row on the FREE TRIAL plan (tier=PlanTier.PLAN1) with
    status=TRIAL, ends_at set 7 days (MembershipPlan.trial_days) out.

    Authenticated only — same reasoning as payments.views.
    CheckoutInitiationView: a trial must be attributed to a real user, so
    this view does NOT override authentication_classes —
    SessionAuthentication (the project default) stays fully in effect,
    INCLUDING its CSRF check. The calling frontend sends the CSRF token
    exactly like any other authenticated same-origin POST (see
    memberships/templates/memberships/prueba_gratis.html + static/site/js/
    site.js's fetch/X-CSRFToken pattern, the same one 4B-3 built for the
    offering "Comprar" button).

    ONE TRIAL PER USER, EVER: rejected if the user already has ANY
    Subscription with is_trial=True, regardless of its current status —
    used, expired, or cancelled all count, because is_trial is set once
    at creation and never changes afterward (unlike `status`, which does
    change over a subscription's life — see Subscription.is_trial's own
    docstring for why the check is anchored to that field instead).
    Also rejected if the user currently has any other active subscription
    (a real paid plan) — no point trialing what they already pay for.

    RACE-SAFETY: the DB constraint Subscription.Meta.constraints'
    `one_trial_subscription_per_user` (a partial UniqueConstraint on
    `user` WHERE is_trial=True) is the actual guard against two
    concurrent double-submits creating two trial rows — the upfront
    `.exists()` check below is just what makes the common, non-race case
    return a clean 409 instead of a raw IntegrityError. Both paths return
    the identical response.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        plan = MembershipPlan.objects.filter(tier=PlanTier.PLAN1, is_active=True).first()
        if plan is None:
            return Response(
                {'detail': 'La prueba gratuita no está disponible en este momento.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if Subscription.objects.filter(user=request.user, is_trial=True).exists():
            return Response(
                {'detail': 'Ya usaste tu prueba gratuita — no se puede repetir.'},
                status=status.HTTP_409_CONFLICT,
            )

        if user_has_any_active_paid_plan(request.user):
            return Response(
                {'detail': 'Ya tenés una membresía activa — no hace falta la prueba gratuita.'},
                status=status.HTTP_409_CONFLICT,
            )

        now = timezone.now()
        trial_ends_at = now + timedelta(days=plan.trial_days)

        try:
            with transaction.atomic():
                subscription = Subscription.objects.create(
                    user=request.user, plan=plan, status=SubscriptionStatus.TRIAL,
                    starts_at=now, ends_at=trial_ends_at, trial_ends_at=trial_ends_at,
                    is_trial=True,
                )
        except IntegrityError:
            # The one_trial_subscription_per_user constraint caught a
            # concurrent double-submit that slipped past the .exists()
            # check above — same rejection as the non-race case.
            return Response(
                {'detail': 'Ya usaste tu prueba gratuita — no se puede repetir.'},
                status=status.HTTP_409_CONFLICT,
            )

        return Response({'ends_at': subscription.ends_at}, status=status.HTTP_201_CREATED)
