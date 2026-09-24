from django.urls import path

from . import views

app_name = 'payments'

urlpatterns = [
    # Mercado Pago return pages (Phase 4B-4) — re-verify against MP's API
    # on load; see views.py's _handle_payment_return.
    path('pago/exito/', views.pago_exito, name='pago_exito'),
    path('pago/pendiente/', views.pago_pendiente, name='pago_pendiente'),
    path('pago/error/', views.pago_error, name='pago_error'),
]
