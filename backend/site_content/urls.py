from django.urls import path

from . import public_views

app_name = 'site_content'

urlpatterns = [
    path('propuestas/<slug:slug>/', public_views.offering_detail, name='offering_detail'),
]
