from django.urls import path

from . import public_views

app_name = 'site_content'

urlpatterns = [
    path('propuestas/<slug:slug>/', public_views.offering_detail, name='offering_detail'),
    # Purchase-gated PDF download — under /propuestas/, already in nginx's
    # Django allowlist (and its no-store Cache-Control).
    path('propuestas/<slug:slug>/descargar/', public_views.offering_download, name='offering_download'),
]
