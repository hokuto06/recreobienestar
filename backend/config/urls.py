from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.static import serve as _serve_static

admin.site.site_header = 'Recreo Bienestar'
admin.site.site_title = 'Recreo Bienestar — Administración'
admin.site.index_title = 'Panel de administración'

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    # Read-only public API. No write endpoints exist anywhere under this
    # prefix — see catalog/views.py and memberships/views.py.
    path('api/', include('config.api_urls')),
    # Public member-facing routes: /registro/, /ingresar/, /salir/,
    # /recuperar-clave/..., /mi-cuenta/... (accounts.urls) and
    # /videoteca/, /videos/<slug>/ (catalog.urls). The landing page itself
    # ('/') is NOT here — it's the existing static site, served directly
    # by nginx and never routed to this container.
    path('', include('accounts.urls')),
    path('', include('catalog.urls')),
]

def serve_media(request, path):
    """Thin wrapper instead of passing document_root=settings.MEDIA_ROOT
    directly in urlpatterns: that kwarg dict is built once, at URLconf
    import time, permanently baking in whatever MEDIA_ROOT was at that
    moment — harmless in production (MEDIA_ROOT never changes at runtime)
    but wrong for tests using override_settings(MEDIA_ROOT=...), and
    generally more correct to read the setting fresh per request."""
    return _serve_static(request, path, document_root=settings.MEDIA_ROOT)


# Bug fix (2026-08-10): nginx's /media/ location (nginx/conf.d/
# recreobienestar.conf) already proxies to this container expecting Django
# to serve MEDIA_ROOT — but no route ever existed to do that in
# production. django.conf.urls.static.static() only registers one when
# DEBUG=True, so every uploaded file 404'd the instant someone actually
# viewed it (masked until now because the STORAGES bug above meant no
# upload had ever succeeded). django.views.static.serve is the standard,
# documented, non-DEBUG-gated way to do this for a project with no S3/CDN
# for media yet — fine at this traffic/media volume; revisit if that
# changes.
urlpatterns += [
    path('media/<path:path>', serve_media),
]
