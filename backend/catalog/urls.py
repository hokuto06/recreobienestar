from django.urls import path

from . import public_views

app_name = 'catalog'

urlpatterns = [
    path('videoteca/', public_views.video_library, name='video_library'),
    path('videos/<slug:slug>/', public_views.video_detail, name='video_detail'),
    path('videos/<slug:slug>/completado/', public_views.mark_video_completed, name='mark_video_completed'),
    path('videos/<slug:slug>/favorito/', public_views.toggle_favorite_view, name='toggle_favorite'),
    path('programas/<slug:slug>/', public_views.program_detail, name='program_detail'),
]
