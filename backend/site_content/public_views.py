"""
Phase 4B-3: the one Django-rendered page for an Offering — lets a
logged-in member actually start a real Mercado Pago checkout for it.
Mirrors catalog.public_views.program_detail's shape (a plain function
view + its own template) rather than inventing a new pattern; see that
view for the sibling case (Program instead of Offering).
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

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
