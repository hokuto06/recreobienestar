from django.conf import settings
from django.contrib import admin
from django.urls import include, path

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
