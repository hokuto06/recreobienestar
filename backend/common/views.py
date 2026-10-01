from urllib.parse import urlsplit

from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme


def _safe_back_path(request):
    """The path of the page the user came from (the Referer), or None.
    Only a same-site http(s) URL is accepted, and only its path is kept —
    never its query string or fragment, so nothing from the request beyond
    a validated internal path ends up in the page."""
    referer = request.META.get('HTTP_REFERER', '')
    if not referer or not url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return None
    path = urlsplit(referer).path
    if not path.startswith('/') or path.startswith('//'):
        return None
    return path


def csrf_failure(request, reason=''):
    """CSRF_FAILURE_VIEW: a friendly replacement for Django's bare "CSRF
    verification failed" page. The usual cause here is a login in another
    tab: Django rotates the CSRF cookie on login, so a form left open
    elsewhere stops matching. Still a 403, and `reason` (Django's internal
    diagnosis) is deliberately not shown — it's already logged by
    django.security.csrf. No submitted data is read or echoed."""
    return render(
        request, '403_csrf.html', {'back_path': _safe_back_path(request)}, status=403,
    )
