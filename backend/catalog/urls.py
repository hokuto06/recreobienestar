from django.urls import path

from . import public_views

app_name = 'catalog'

urlpatterns = [
    path('videoteca/', public_views.video_library, name='video_library'),
    path('videos/<slug:slug>/', public_views.video_detail, name='video_detail'),
]
