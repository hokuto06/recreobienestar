from django.urls import include, path
from rest_framework.routers import DefaultRouter

from catalog.views import CategoryListView, ProgramListView, VideoViewSet
from memberships.views import MembershipPlanListView
from site_content.views import OfferingListView, SiteSettingsView, TestimonialListView

router = DefaultRouter()
router.register('videos', VideoViewSet, basename='video')

urlpatterns = [
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('programs/', ProgramListView.as_view(), name='program-list'),
    path('plans/', MembershipPlanListView.as_view(), name='plan-list'),
    path('offerings/', OfferingListView.as_view(), name='offering-list'),
    path('testimonials/', TestimonialListView.as_view(), name='testimonial-list'),
    path('site-settings/', SiteSettingsView.as_view(), name='site-settings'),
    path('', include(router.urls)),
]
