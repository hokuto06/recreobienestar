"""
Phase 4B-3: the one Django-rendered page for an Offering — lets a
logged-in member actually start a real Mercado Pago checkout for it.
Mirrors catalog.public_views.program_detail's shape (a plain function
view + its own template) rather than inventing a new pattern; see that
view for the sibling case (Program instead of Offering).
"""
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render

from payments.models import OfferingPurchase

from .models import Offering


@login_required
def offering_detail(request, slug):
    """GET /propuestas/<slug>/ — an Offering's own page: name, price,
    description, and the real "Comprar" button (see static/site/js/
    site.js's checkout block for the fetch()-to-/api/checkout/ flow that
    button triggers, and payments.views.CheckoutInitiationView for what
    happens server-side).

    @login_required rather than a public view: a purchase must be
    attributed to a real user (see CheckoutInitiationView's own
    docstring), so there is no legitimate reason to ever show this page
    to an anonymous visitor. Django's decorator already does exactly the
    right thing for that case, for free: redirect to settings.LOGIN_URL
    (/ingresar/) with ?next=<this-page>, so a visitor who isn't logged in
    yet lands right back here — on the offering they actually wanted —
    after logging in, instead of a raw 403 or a dead end.

    Inactive/nonexistent slugs both 404, same as program_detail's Program
    lookup — this mirrors GET /api/offerings/ (site_content.views.
    OfferingListView), which already only ever lists is_active=True
    offerings, so a slug that doesn't resolve here was never actually
    reachable from the home page's cards either.
    """
    offering = get_object_or_404(Offering, slug=slug, is_active=True)
    return render(request, 'site_content/offering_detail.html', {'offering': offering})


@login_required
def offering_download(request, slug):
    """GET /propuestas/<slug>/descargar/ — the offering's PDF, ONLY for a
    user with a COMPLETED purchase of it. The file lives in private storage
    (site_content/storage.py) with no public URL; this is the only way out.

    Anonymous: login redirect (via @login_required, same as offering_detail
    — reveals nothing, it happens for any slug). Logged in but no COMPLETED
    purchase (never bought, PENDING, FAILED, REFUNDED), no file, missing
    file, or unknown slug: all the same 404, so a non-buyer can't tell
    whether a file exists. Access is indefinite: a completed purchase keeps
    working even if Carla later deactivates the offering (no is_active
    filter here, on purpose)."""
    offering = Offering.objects.filter(slug=slug).first()
    if offering is None or not offering.deliverable:
        raise Http404
    bought = OfferingPurchase.objects.completed().filter(user=request.user, offering=offering).exists()
    if not bought:
        raise Http404
    try:
        handle = offering.deliverable.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404
    return FileResponse(
        handle, as_attachment=True, filename=f'{offering.slug}.pdf', content_type='application/pdf',
    )
