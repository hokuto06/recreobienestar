from django.urls import path

from . import public_views

app_name = 'memberships'

urlpatterns = [
    path('prueba-gratis/', public_views.prueba_gratis, name='prueba_gratis'),
    # Phase 5B-2a: paid-plan signup page + Mercado Pago's preapproval
    # back_url. estado/ must stay above <slug:slug>/.
    path('membresia/estado/', public_views.membresia_estado, name='membresia_estado'),
    path('membresia/<slug:slug>/', public_views.membresia_detail, name='membresia_detail'),
    # Phase 5B-2b: manage/cancel the paid subscription. Lives under the
    # account area's /mi-cuenta/ prefix (already in nginx's allowlist).
    path('mi-cuenta/suscripcion/', public_views.mi_suscripcion, name='mi_suscripcion'),
]
