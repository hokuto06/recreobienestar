from django.urls import path

from . import public_views

app_name = 'memberships'

urlpatterns = [
    path('prueba-gratis/', public_views.prueba_gratis, name='prueba_gratis'),
]
