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
]
