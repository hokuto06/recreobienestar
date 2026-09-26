import logging
from datetime import timedelta

import mercadopago
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.choices import PlanTier, SubscriptionStatus

from .models import MembershipPlan, Subscription
from .serializers import MembershipPlanSerializer
from .services import (
    billing_cadence_for_plan, user_has_active_paid_subscription, user_has_any_active_paid_plan,
)

logger = logging.getLogger(__name__)


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


def resolve_purchasable_plan(reference):
    """Looks up an active MembershipPlan by slug OR numeric id (same shape
    as payments.views._resolve_active_offering). Returns None (never
    raises) if it doesn't exist or isn't active. Does NOT exclude the
    free-trial plan — callers reject that one explicitly, with its own
    message."""
    if reference is None:
        return None
    if isinstance(reference, int) or (isinstance(reference, str) and reference.isdigit()):
        return MembershipPlan.objects.filter(pk=reference, is_active=True).first()
    return MembershipPlan.objects.filter(slug=reference, is_active=True).first()


# Mercado Pago copies a preapproval's external_reference onto EVERY
# recurring charge, and those charges also arrive at the payments webhook
# as ordinary `type=payment` notifications. payments.services.
# apply_payment_to_purchase reads external_reference as a bare
# OfferingPurchase id (int(...)), so a bare Subscription id would be taken
# for an unrelated purchase whenever the two independent id counters
# collide. The "sub-" prefix makes int() fail there, which that function
# already treats as invalid_external_reference: logged, nothing read or
# changed. Format: "sub-<Subscription.id>", e.g. "sub-5".
SUBSCRIPTION_EXTERNAL_REFERENCE_PREFIX = 'sub-'


def subscription_external_reference(subscription):
    return f'{SUBSCRIPTION_EXTERNAL_REFERENCE_PREFIX}{subscription.id}'


# How long a PENDING signup's MP preapproval is reused instead of creating
# another one — see StartSubscriptionView's DUPLICATE GUARD.
PENDING_SIGNUP_REUSE_WINDOW = timedelta(hours=1)


def _recent_pending_signup(user, plan):
    """The user's most recent PENDING subscription to `plan` that already
    has an MP preapproval + init_point, was created within
    PENDING_SIGNUP_REUSE_WINDOW, and was quoted at the plan's CURRENT
    price/currency — or None."""
    return (
        Subscription.objects.filter(
            user=user, plan=plan, status=SubscriptionStatus.PENDING, is_trial=False,
            amount=plan.price, currency=plan.currency,
            created_at__gte=timezone.now() - PENDING_SIGNUP_REUSE_WINDOW,
        )
        .exclude(mp_preapproval_id='').exclude(mp_init_point='')
        .order_by('-created_at').first()
    )


class StartSubscriptionView(APIView):
    """POST /api/subscribe/ — starts a paid-plan signup (Phase 5B-2a):
    creates a Mercado Pago preapproval (recurring charge authorization)
    and hands back its init_point for the browser to redirect to. Body:
    {"plan": "<slug-or-id>"}.

    Mirrors payments.views.CheckoutInitiationView: authenticated only,
    SessionAuthentication (and its CSRF check) left fully in effect, and
    price/currency NEVER read from the request — plan.price/plan.currency
    straight from the DB are what's sent to MP and snapshotted on the row.

    NO ACCESS IS GRANTED HERE. The Subscription is created PENDING, which
    is not in common.choices.ENTITLED_STATUSES, so is_active() — and with
    it can_access_video() — stays False for it. Only MP's confirmation
    (the subscription webhook, Phase 5B-2b) will ever move it to ACTIVE.

    Rejections: unknown/inactive plan (404), the free-trial plan (400),
    a plan whose duration_days has no known billing cadence (503 — see
    memberships.services.billing_cadence_for_plan), an account with no
    email (400 — MP requires payer_email), and a user who already has an
    active paid subscription (409 — no plan switching in this phase).

    AN ACTIVE FREE TRIAL IS NOT TOUCHED HERE. Signing up only means the
    member was sent to MP — they may still abandon MP's page. The trial
    ends when MP confirms the first charge: 5B-2b's confirmation path
    calls memberships.services.supersede_active_trial() at that point.

    DUPLICATE GUARD: if the user already has a PENDING subscription for
    the same plan, at the same price/currency, with an MP preapproval,
    created within PENDING_SIGNUP_REUSE_WINDOW (1 hour), its stored
    init_point is returned instead of minting a second recurring-charge
    authorization at MP (double-click, refresh, back button). Anything
    older, or on a different price, gets a fresh preapproval. This is
    not a cleanup mechanism for stale PENDING rows — that's 5B-2b's.

    ON MP FAILURE: the PENDING row is marked EXPIRED with ends_at=now
    rather than deleted (keeps an audit trail of the attempt, same as
    CheckoutInitiationView's FAILED purchase) and rather than CANCELLED —
    CANCELLED is in ENTITLED_STATUSES, so it must never be used for a
    subscription that was never paid for.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        plan = resolve_purchasable_plan(request.data.get('plan'))
        if plan is None:
            return Response(
                {'detail': 'Plan no encontrado o no disponible.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if plan.tier == PlanTier.PLAN1:
            return Response(
                {'detail': 'La prueba gratuita no se contrata como suscripción paga.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cadence = billing_cadence_for_plan(plan)
        if cadence is None:
            logger.error(
                'MembershipPlan %s has duration_days=%r with no known billing cadence — '
                'refusing subscription signup',
                plan.id, plan.duration_days,
            )
            return Response(
                {'detail': 'Este plan no está disponible para suscribirse en este momento.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        frequency, frequency_type = cadence

        if not request.user.email:
            return Response(
                {'detail': 'Tu cuenta no tiene un email cargado — agregalo en Mi cuenta para suscribirte.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user_has_active_paid_subscription(request.user):
            return Response(
                {'detail': 'Ya tenés una membresía activa. Por ahora no se puede cambiar de plan.'},
                status=status.HTTP_409_CONFLICT,
            )

        reusable = _recent_pending_signup(request.user, plan)
        if reusable is not None:
            return Response(
                {'init_point': reusable.mp_init_point, 'preapproval_id': reusable.mp_preapproval_id},
                status=status.HTTP_200_OK,
            )

        subscription = Subscription.objects.create(
            user=request.user, plan=plan, status=SubscriptionStatus.PENDING,
            starts_at=timezone.now(), amount=plan.price, currency=plan.currency,
        )

        base_url = f'{request.scheme}://{request.get_host()}'
        preapproval_data = {
            'reason': f'{plan.name} — Recreo Bienestar',
            # Carries our subscription id so 5B-2b's webhook can map MP's
            # notifications back to this exact row (mp_preapproval_id is
            # the other anchor). PREFIXED on purpose — see
            # subscription_external_reference.
            'external_reference': subscription_external_reference(subscription),
            'payer_email': request.user.email,
            # No free_trial here on purpose: the free trial is entirely
            # ours (Phase 5B-1) — MP bills from day one.
            'auto_recurring': {
                'frequency': frequency,
                'frequency_type': frequency_type,
                'transaction_amount': float(plan.price),
                'currency_id': plan.currency,
            },
            'back_url': f'{base_url}/membresia/estado/',
            'status': 'pending',
        }

        try:
            sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
            result = sdk.preapproval().create(preapproval_data)
            result.raise_for_status()
            preapproval = result['response']
            if not preapproval.get('id') or not preapproval.get('init_point'):
                raise ValueError('preapproval response missing id/init_point')
        except Exception:
            logger.exception(
                'Mercado Pago preapproval creation failed for Subscription %s', subscription.id,
            )
            subscription.status = SubscriptionStatus.EXPIRED
            subscription.ends_at = timezone.now()
            subscription.save(update_fields=['status', 'ends_at', 'updated_at'])
            return Response(
                {'detail': 'No se pudo iniciar la suscripción. Intentá de nuevo en unos minutos.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        subscription.mp_preapproval_id = str(preapproval['id'])
        subscription.mp_status = preapproval.get('status', '') or ''
        subscription.mp_init_point = preapproval['init_point']
        subscription.save(update_fields=[
            'mp_preapproval_id', 'mp_status', 'mp_init_point', 'updated_at',
        ])

        return Response(
            {'init_point': preapproval['init_point'], 'preapproval_id': subscription.mp_preapproval_id},
            status=status.HTTP_201_CREATED,
        )
