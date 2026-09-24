"""
Phase 4B-1 added checkout INITIATION (CheckoutInitiationView). Phase 4B-2
added the WEBHOOK receiver (MercadoPagoWebhookView). Phase 4B-4 adds the
return-URL verification views (pago_exito/pago_pendiente/pago_error) as a
COMPLEMENT to the webhook, not a replacement — both paths end up calling
the exact same purchase-completion logic (payments.services.
apply_payment_to_purchase), never a second copy of it. See
MercadoPagoWebhookView's docstring for the security model both paths
share; it is deliberately much more defensive than the rest of this file.
"""
import logging

import mercadopago
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET
from mercadopago.webhook import InvalidWebhookSignatureError, WebhookSignatureValidator
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from site_content.models import Offering

from . import services
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
            # Real, verified return pages (payments/urls.py -> pago_exito/
            # pago_pendiente/pago_error, Phase 4B-4) — see those views for
            # the re-query-MP verification that runs when the buyer lands
            # on one of these.
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


def _extract_data_id(request):
    """The payment id Mercado Pago is notifying about. Per MP's docs it can
    arrive either as a `data.id` query-string param (the current
    Checkout Pro/Bricks format) or nested in the JSON body as
    `{"data": {"id": ...}}` — this checks both, query param first."""
    data_id = request.query_params.get('data.id')
    if not data_id and isinstance(request.data, dict):
        body_data = request.data.get('data')
        if isinstance(body_data, dict):
            data_id = body_data.get('id')
    return str(data_id) if data_id else None


def _fetch_mp_payment(payment_id):
    """Re-queries Mercado Pago's Payments API for `payment_id` and
    returns the raw, authoritative payment dict — the ONLY source of
    truth either caller below ever acts on (never the webhook's request
    body, never the return-URL's query string). Shared by
    MercadoPagoWebhookView and the /pago/* return views below, both of
    which then hand the result to payments.services.
    apply_payment_to_purchase().

    Raises whatever the SDK raises on failure (network error, a non-2xx
    status via raise_for_status(), including MPNotFoundError for an
    unknown/fake payment id such as the one Mercado Pago's own webhook
    "simulate" button sends) — callers decide how to react."""
    sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
    result = sdk.payment().get(payment_id)
    result.raise_for_status()
    return result['response']


class MercadoPagoWebhookView(APIView):
    """POST /api/mercadopago/webhook/ — Mercado Pago's payment-notification
    receiver (Phase 4B-2). The single most security-sensitive endpoint in
    this project: it is publicly reachable on the open internet AND it is
    the only thing that ever completes a purchase and thereby unlocks paid
    video access (see memberships.services.can_access_video).

    PUBLIC BUT NOT TRUSTED — contrast with site_content.views.
    ContactMessageCreateView, the project's other anonymous endpoint:
      - authentication_classes = [] / permission_classes = [AllowAny]:
        Mercado Pago is a server, not a browser with a session, so
        SessionAuthentication (and its CSRF check) doesn't apply — same
        reasoning as ContactMessageCreateView. DRF views are already
        CSRF-exempt at the Django-middleware level, so no @csrf_exempt is
        needed either.
      - Unlike ContactMessageCreateView, "anonymous" here does NOT mean
        "low stakes, validate the input and move on". Trust is established
        cryptographically, inside this handler, on EVERY request, via two
        independent checks, in order:
          1) signature verification (below) — anything that fails this is
             rejected with 401 before a single row is read or written.
          2) even once signature-valid, the incoming body's reported
             payment STATUS is never trusted — see the payment.get()
             re-query in post(). Only Mercado Pago's own API response ever
             decides a purchase's fate.

    SIGNATURE VERIFICATION: uses the official mercadopago SDK's
    WebhookSignatureValidator (mercadopago/webhook/validator.py, installed
    at mercadopago==3.6.0, confirmed by reading that module directly)
    rather than a hand-rolled implementation, since it already implements
    MP's documented scheme exactly:
      - parses `ts` and the `v1` hash out of the `x-signature` header
        (format: `ts=<unix-seconds>,v1=<hex-hmac>`)
      - builds the manifest string `id:<data.id>;request-id:<x-request-id>;
        ts:<ts>;` (any component whose source value is absent is omitted
        from the manifest, per MP's spec) — `data.id` is lower-cased first,
        per MP's documented note that some frameworks hand it over
        uppercase
      - computes HMAC-SHA256 of that manifest with
        settings.MERCADOPAGO_WEBHOOK_SECRET
      - compares it to the `v1` hash with hmac.compare_digest (constant
        time — never `==`)
    A missing header, malformed header, or mismatched hash all raise
    InvalidWebhookSignatureError, which short-circuits to a 401 with
    nothing touched. The rejection is logged with the failure *reason* and
    x-request-id only — never the secret, never the raw signature header.

    NEVER TRUSTS THE PAYLOAD'S STATUS: after the signature passes, this
    view takes ONLY the payment id from the notification and re-queries
    `sdk.payment().get(...)` — Mercado Pago's authoritative payment object
    — using MERCADOPAGO_ACCESS_TOKEN. Every decision below (status mapping,
    amount check) reads from that re-queried response, never from
    request.data.

    IDEMPOTENCY / CONCURRENCY: MP retries notifications (sometimes several
    times, sometimes out of order). The purchase row is locked with
    select_for_update() inside an atomic transaction, and an
    already-COMPLETED purchase is always a no-op — regardless of what the
    new notification says — so a stale/duplicate/out-of-order notification
    can never re-process or downgrade a settled purchase, and two
    concurrent notifications for the same purchase can't both complete it.

    RESPONSE CODES: 200 for every notification this view has handled OR
    deliberately ignored (unknown purchase, missing external_reference,
    already-completed no-op, amount mismatch) — MP stops retrying on 2xx,
    and none of those cases benefit from a retry. 401 is reserved
    exclusively for signature failure. 502 is for a genuine transient
    failure (the re-query to MP itself failed) where a retry IS wanted.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        x_signature = request.headers.get('x-signature')
        x_request_id = request.headers.get('x-request-id')
        data_id = _extract_data_id(request)

        try:
            WebhookSignatureValidator.validate(
                x_signature=x_signature,
                x_request_id=x_request_id,
                data_id=data_id.lower() if data_id else data_id,
                secret=settings.MERCADOPAGO_WEBHOOK_SECRET,
            )
        except InvalidWebhookSignatureError as exc:
            logger.warning(
                'Mercado Pago webhook rejected: invalid signature (%s), x-request-id=%s',
                exc.reason.value, x_request_id,
            )
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        if not data_id:
            # Signature-valid but no payment id to act on (e.g. a
            # non-payment topic notification) — nothing to do, but it's a
            # legitimately signed request, so acknowledge it normally.
            logger.info('Mercado Pago webhook: signature OK, no data.id present — nothing to do')
            return Response(status=status.HTTP_200_OK)

        try:
            payment = _fetch_mp_payment(data_id)
        except Exception:
            logger.exception('Mercado Pago webhook: payment lookup failed for data.id=%s', data_id)
            return Response(status=status.HTTP_502_BAD_GATEWAY)

        # Every decision from here on — mapping to a purchase, amount
        # check, status mapping, idempotent completion — lives in
        # payments.services.apply_payment_to_purchase, shared with the
        # /pago/* return views below. Its return value isn't branched on
        # here: this view's response contract is unconditionally 200 for
        # any signature-valid notification whose payment lookup
        # succeeded, exactly as before this function existed — MP stops
        # retrying on 2xx, and none of apply_payment_to_purchase's
        # possible outcomes (unknown purchase, invalid external_reference,
        # already-completed, amount mismatch, or a normal completion)
        # benefit from a retry.
        services.apply_payment_to_purchase(payment, fallback_payment_id=data_id)
        return Response(status=status.HTTP_200_OK)


def _extract_return_payment_id(request):
    """The payment id from Mercado Pago's return-URL query string
    (?payment_id=...). MP has been observed sending the literal string
    "null" here (e.g. a checkout abandoned before any real payment
    existed) — treated the same as the param being absent entirely,
    never looked up."""
    payment_id = request.GET.get('payment_id')
    if not payment_id or payment_id.strip().lower() == 'null':
        return None
    return payment_id


def _handle_payment_return(request):
    """Shared verification for all three /pago/* return views below.
    Mercado Pago's back_urls (success/pending/failure) only reflect what
    MP believed the outcome was AT REDIRECT TIME — exactly like the
    webhook, this NEVER trusts that, or any other query-string param
    (status, collection_status, etc.). Only `payment_id` is read from the
    URL, purely as a lookup key; every actual decision comes from
    re-querying Mercado Pago (_fetch_mp_payment) and
    payments.services.apply_payment_to_purchase — the same function the
    webhook (4B-2) uses, so a purchase completes identically regardless
    of whether the webhook or this return visit gets there first (or
    both — idempotent either way).

    Returns (purchase_or_None, outcome) where outcome is one of:
      'completed' — purchase.status is now COMPLETED (or already was).
      'pending'   — still PENDING (includes the amount-mismatch case,
                    which deliberately stays PENDING for manual review)
                    or REFUNDED/an unmapped MP status.
      'failed'    — purchase.status is FAILED.
      'unconfirmed' — nothing could be verified (missing/garbage
                    payment_id, MP lookup failed e.g. a fake/test id,
                    unknown or invalid external_reference, or the
                    purchase belongs to a different user) — `purchase`
                    is always None here, and NOTHING was changed.

    The template shown is always based on the ACTUAL post-verification
    outcome, never on which of the three URLs the browser happened to
    land on — MP's own redirect choice is just a hint, not trusted.
    """
    payment_id = _extract_return_payment_id(request)
    if payment_id is None:
        return None, 'unconfirmed'

    try:
        payment = _fetch_mp_payment(payment_id)
    except Exception:
        # Covers a fake/test payment id (MPNotFoundError — same case MP's
        # own webhook "simulate" button triggers) and any transient
        # lookup failure alike: either way, nothing to show yet, nothing
        # to change, and no 500 — the buyer can simply retry the page
        # later, or the webhook may resolve it independently.
        logger.info('Mercado Pago return: payment lookup failed for payment_id=%s', payment_id)
        return None, 'unconfirmed'

    outcome = services.apply_payment_to_purchase(
        payment, fallback_payment_id=payment_id, expected_user=request.user,
    )

    if outcome.purchase is None:
        # reason is 'wrong_user', 'invalid_external_reference', or
        # 'unknown_purchase' — all deliberately collapsed into the same
        # neutral outcome here, so a wrong-user attempt is never
        # distinguishable from an honestly-garbled URL.
        return None, 'unconfirmed'

    purchase = outcome.purchase
    if purchase.status == PurchaseStatus.COMPLETED:
        return purchase, 'completed'
    if purchase.status == PurchaseStatus.FAILED:
        return purchase, 'failed'
    return purchase, 'pending'


def _render_payment_return(request, purchase, outcome):
    if outcome == 'completed':
        return render(request, 'payments/pago_exito.html', {'purchase': purchase})
    if outcome == 'pending':
        return render(request, 'payments/pago_pendiente.html', {'purchase': purchase})
    if outcome == 'failed':
        return render(request, 'payments/pago_error.html', {'purchase': purchase})
    return render(request, 'payments/pago_no_confirmado.html')


# ── Return pages (Phase 4B-4) ──────────────────────────────────────────
# Mercado Pago's Checkout Pro redirects the buyer's browser back to one of
# these three back_urls (set at preference-creation time — see
# CheckoutInitiationView) after they finish paying. This is a COMPLEMENT
# to the webhook (4B-2), not a replacement: in this project's sandbox,
# webhook notifications have been observed not arriving reliably (see the
# 4B-4 diagnosis), so a purchase that would otherwise stay stuck PENDING
# forever now also gets a chance to settle the moment the buyer comes
# back — via the exact same trust model (re-query MP, never trust the
# URL) and the exact same completion logic (payments.services.
# apply_payment_to_purchase) as the webhook. Login-required so
# request.user exists for the ownership check in _handle_payment_return —
# an anonymous visitor is sent to /ingresar/?next=... exactly like the
# offering-detail page (site_content.public_views.offering_detail), never
# a raw 403.
@login_required
@require_GET
def pago_exito(request):
    purchase, outcome = _handle_payment_return(request)
    return _render_payment_return(request, purchase, outcome)


@login_required
@require_GET
def pago_pendiente(request):
    purchase, outcome = _handle_payment_return(request)
    return _render_payment_return(request, purchase, outcome)


@login_required
@require_GET
def pago_error(request):
    purchase, outcome = _handle_payment_return(request)
    return _render_payment_return(request, purchase, outcome)
