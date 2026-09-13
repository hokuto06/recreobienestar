from django.urls import path

from . import views

app_name = 'payments'

urlpatterns = [
    # Placeholder Mercado Pago return pages (see views.py) — real
    # templates land in Phase 4B-3.
    path('pago/exito/', views.pago_exito, name='pago_exito'),
    path('pago/pendiente/', views.pago_pendiente, name='pago_pendiente'),
    path('pago/error/', views.pago_error, name='pago_error'),
]
