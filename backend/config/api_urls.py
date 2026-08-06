from django.urls import include, path
from rest_framework.routers import DefaultRouter

from catalog.views import CategoryListView, ProgramListView, VideoViewSet
from memberships.views import MembershipPlanListView

router = DefaultRouter()
router.register('videos', VideoViewSet, basename='video')

urlpatterns = [
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('programs/', ProgramListView.as_view(), name='program-list'),
    path('plans/', MembershipPlanListView.as_view(), name='plan-list'),
    path('', include(router.urls)),
]
