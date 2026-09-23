"""
Phase 4B-1 added checkout INITIATION (CheckoutInitiationView). Phase 4B-2
adds the WEBHOOK receiver (MercadoPagoWebhookView) — the only view in this
module that actually completes a purchase and, by extension, unlocks paid
video access. See MercadoPagoWebhookView's docstring for its security
model; it is deliberately much more defensive than the rest of this file.
"""
import logging
from decimal import Decimal, InvalidOperation

import mercadopago
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from mercadopago.webhook import InvalidWebhookSignatureError, WebhookSignatureValidator
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
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


def _amount_mismatches(purchase, payment):
    """True if what Mercado Pago says was actually paid doesn't match the
    price snapshot taken at checkout time (payments.views.
    CheckoutInitiationView). Defense in depth against a tampered/replayed
    notification trying to unlock a purchase for less than it costs.

    Any comparison failure (missing/malformed transaction_amount, no price
    snapshot to compare against — the latter can't happen for a 4B-1+
    purchase, only theoretically for a pre-4B-1 hand-created one) is
    treated as a mismatch: fail closed, never fail open."""
    if purchase.amount is None:
        return True
    try:
        reported_amount = Decimal(str(payment.get('transaction_amount'))).quantize(Decimal('0.01'))
    except (TypeError, InvalidOperation):
        return True
    reported_currency = (payment.get('currency_id') or '').upper()
    expected_currency = (purchase.currency or '').upper()
    return reported_amount != purchase.amount or reported_currency != expected_currency


def _map_purchase_status(mp_status):
    """Mercado Pago's payment.status vocabulary -> this app's
    PurchaseStatus. Returns None for a recognized-but-not-actionable MP
    status (e.g. authorized, in_mediation, charged_back) — the caller
    still records mp_status/mp_payment_id but leaves `status` untouched
    rather than guessing at a mapping the product hasn't decided on."""
    if mp_status == 'approved':
        return PurchaseStatus.COMPLETED
    if mp_status in ('rejected', 'cancelled'):
        return PurchaseStatus.FAILED
    if mp_status in ('pending', 'in_process'):
        return PurchaseStatus.PENDING
    if mp_status == 'refunded':
        return PurchaseStatus.REFUNDED
    return None


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
            sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
            result = sdk.payment().get(data_id)
            result.raise_for_status()
        except Exception:
            logger.exception('Mercado Pago webhook: payment lookup failed for data.id=%s', data_id)
            return Response(status=status.HTTP_502_BAD_GATEWAY)

        payment = result['response']
        mp_payment_id = str(payment.get('id') or data_id)
        mp_status = payment.get('status', '')
        external_reference = payment.get('external_reference')

        try:
            purchase_id = int(external_reference)
        except (TypeError, ValueError):
            logger.warning(
                'Mercado Pago webhook: payment %s has missing/invalid external_reference=%r — '
                'ignoring, no purchase created or changed',
                mp_payment_id, external_reference,
            )
            return Response(status=status.HTTP_200_OK)

        with transaction.atomic():
            purchase = OfferingPurchase.objects.select_for_update().filter(pk=purchase_id).first()
            if purchase is None:
                logger.warning(
                    'Mercado Pago webhook: payment %s references unknown OfferingPurchase %s',
                    mp_payment_id, purchase_id,
                )
                return Response(status=status.HTTP_200_OK)

            if purchase.status == PurchaseStatus.COMPLETED:
                # Idempotent no-op — a retried, duplicate, or stale/
                # out-of-order notification for a purchase that already
                # unlocked access. Never re-process, never downgrade.
                logger.info(
                    'Mercado Pago webhook: purchase %s already COMPLETED — ignoring payment %s (%s)',
                    purchase.id, mp_payment_id, mp_status,
                )
                return Response(status=status.HTTP_200_OK)

            purchase.mp_payment_id = mp_payment_id
            purchase.mp_status = mp_status

            if _amount_mismatches(purchase, payment):
                # Defense in depth (step e): a signature-valid, MP-confirmed
                # payment that nonetheless doesn't match what was quoted at
                # checkout. Never mark COMPLETED off this; leave PENDING
                # (mp_status is saved, so Carla can see what MP actually
                # reported) so it surfaces for manual review in the admin.
                logger.error(
                    'Mercado Pago webhook: AMOUNT/CURRENCY MISMATCH on purchase %s — expected '
                    '%s %s, MP reported %s %s (payment %s, mp_status=%s) — left PENDING for '
                    'manual review',
                    purchase.id, purchase.amount, purchase.currency,
                    payment.get('transaction_amount'), payment.get('currency_id'),
                    mp_payment_id, mp_status,
                )
                purchase.status = PurchaseStatus.PENDING
                purchase.save(update_fields=['mp_payment_id', 'mp_status', 'status', 'updated_at'])
                return Response(status=status.HTTP_200_OK)

            new_status = _map_purchase_status(mp_status)
            if new_status is not None:
                purchase.status = new_status
            purchase.save(update_fields=['mp_payment_id', 'mp_status', 'status', 'updated_at'])

        logger.info(
            'Mercado Pago webhook: purchase %s -> status=%s (mp_status=%s, payment=%s)',
            purchase.id, purchase.status, mp_status, mp_payment_id,
        )
        return Response(status=status.HTTP_200_OK)


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
