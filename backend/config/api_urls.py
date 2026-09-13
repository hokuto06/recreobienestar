from django.urls import include, path
from rest_framework.routers import DefaultRouter

from catalog.views import CategoryListView, ProgramListView, VideoViewSet
from memberships.views import MembershipPlanListView
from payments.views import CheckoutInitiationView
from site_content.views import (
    ContactMessageCreateView, OfferingListView, SiteSettingsView, TestimonialListView,
)

router = DefaultRouter()
router.register('videos', VideoViewSet, basename='video')

urlpatterns = [
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('programs/', ProgramListView.as_view(), name='program-list'),
    path('plans/', MembershipPlanListView.as_view(), name='plan-list'),
    path('offerings/', OfferingListView.as_view(), name='offering-list'),
    path('testimonials/', TestimonialListView.as_view(), name='testimonial-list'),
    path('site-settings/', SiteSettingsView.as_view(), name='site-settings'),
    # The only two non-read-only endpoints under /api/, and deliberate
    # opposites of each other: contacto is anonymous (see
    # ContactMessageCreateView's docstring for why that's safe without
    # session auth/CSRF); checkout is authenticated-only (see
    # CheckoutInitiationView's docstring) and keeps SessionAuthentication —
    # and therefore CSRF — fully enabled.
    path('contacto/', ContactMessageCreateView.as_view(), name='contact-message-create'),
    path('checkout/', CheckoutInitiationView.as_view(), name='checkout-initiate'),
    path('', include(router.urls)),
]
