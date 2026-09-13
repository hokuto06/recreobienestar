"""
Phase 4B-1: checkout INITIATION only. This view creates a Mercado Pago
Checkout Pro preference for a one-time Offering purchase and hands the
frontend the URL to redirect the buyer to. It does NOT:
  - complete a purchase (status stays PENDING; only 4B-2's webhook, or a
    manual admin edit, ever sets COMPLETED)
  - unlock any video access (that already happens automatically, via
    memberships.services.can_access_video, the instant a purchase
    IS COMPLETED — nothing new needed here)
  - send any email
  - handle the Mercado Pago webhook (4B-2)
"""
import logging

import mercadopago
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from site_content.models import Offering

from .models import OfferingPurchase, PurchaseStatus

logger = logging.getLogger(__name__)


def _resolve_active_offering(reference):
    """Looks up an active Offering by slug OR numeric id — whichever
    `reference` looks like. Returns None (never raises) if it doesn't
    exist or isn't active; the view turns that into a clean 404."""
    if reference is None:
        return None
    if isinstance(reference, int) or (isinstance(reference, str) and reference.isdigit()):
        return Offering.objects.filter(pk=reference, is_active=True).first()
    return Offering.objects.filter(slug=reference, is_active=True).first()


class CheckoutInitiationView(APIView):
    """POST /api/checkout/ — starts a Mercado Pago Checkout Pro session for
    one Offering. Body: {"offering": "<slug-or-id>"}.

    Authenticated only — permission_classes below is the OPPOSITE of
    site_content.views.ContactMessageCreateView's anonymous design: a
    purchase must be attributed to a real user, so this view does NOT
    override authentication_classes — SessionAuthentication (the project
    default) stays fully in effect, INCLUDING its CSRF check. The calling
    frontend must send the CSRF token alongside the session cookie (e.g.
    read it from a `{% csrf_token %}`-sourced <meta> tag, per
    config/settings.py's CSRF_COOKIE_HTTPONLY comment) exactly like any
    other authenticated same-origin POST — nothing here exempts it.

    Price/currency are NEVER read from the request: `offering.price` and
    `offering.currency`, straight from the DB, are what's sent to Mercado
    Pago and stored on the purchase — anything resembling a price in the
    POST body is ignored entirely.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        offering = _resolve_active_offering(request.data.get('offering'))
        if offering is None:
            return Response(
                {'detail': 'Propuesta no encontrada o no disponible.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Recorded PENDING before ever calling out to Mercado Pago —
        # amount/currency are a snapshot of `offering`'s CURRENT price,
        # never anything from request.data.
        purchase = OfferingPurchase.objects.create(
            user=request.user, offering=offering, status=PurchaseStatus.PENDING,
            amount=offering.price, currency=offering.currency,
        )

        base_url = f'{request.scheme}://{request.get_host()}'
        preference_data = {
            'items': [{
                'title': offering.name,
                'quantity': 1,
                'unit_price': float(offering.price),
                'currency_id': offering.currency,
            }],
            # Placeholder routes (payments/urls.py) — 4B-3 builds the real
            # success/pending/failure pages; these just need to resolve to
            # SOMETHING today so Mercado Pago has valid URLs to redirect to.
            'back_urls': {
                'success': f'{base_url}/pago/exito/',
                'pending': f'{base_url}/pago/pendiente/',
                'failure': f'{base_url}/pago/error/',
            },
            # Carries our purchase id so 4B-2's webhook can map an
            # incoming payment notification back to this exact row.
            'external_reference': str(purchase.id),
        }

        try:
            sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
            result = sdk.preference().create(preference_data)
            result.raise_for_status()
        except Exception:
            logger.exception(
                'Mercado Pago preference creation failed for OfferingPurchase %s', purchase.id,
            )
            # Documented choice: a failed preference creation marks the
            # purchase FAILED rather than (a) deleting the row — an audit
            # trail of the attempt should survive — or (b) silently
            # leaving it PENDING with no mp_preference_id, which would be
            # indistinguishable in the admin from "checkout still in
            # progress".
            purchase.status = PurchaseStatus.FAILED
            purchase.save(update_fields=['status', 'updated_at'])
            return Response(
                {'detail': 'No se pudo iniciar el pago. Intentá de nuevo en unos minutos.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        preference = result['response']
        purchase.mp_preference_id = preference.get('id', '')
        purchase.save(update_fields=['mp_preference_id', 'updated_at'])

        # Never echo back the access token or any other secret — only
        # what the frontend needs to redirect the buyer.
        return Response(
            {'init_point': preference.get('init_point'), 'preference_id': preference.get('id')},
            status=status.HTTP_201_CREATED,
        )


# ── Placeholder return pages (4B-3 replaces these with real templates) ──
# Mercado Pago needs SOME resolvable back_urls at preference-creation time;
# these exist purely so those URLs 200 instead of 404 while sandbox-testing
# the checkout flow end-to-end. No purchase-status lookup, no template,
# no styling — genuinely just a placeholder.
@require_GET
def pago_exito(request):
    return HttpResponse('Pago aprobado. Esta página se completará en la Fase 4B-3.')


@require_GET
def pago_pendiente(request):
    return HttpResponse('Pago pendiente. Esta página se completará en la Fase 4B-3.')


@require_GET
def pago_error(request):
    return HttpResponse('El pago no se pudo procesar. Esta página se completará en la Fase 4B-3.')
