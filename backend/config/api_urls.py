from django.urls import include, path
from rest_framework.routers import DefaultRouter

from catalog.views import CategoryListView, ProgramListView, VideoViewSet
from memberships.views import MembershipPlanListView, StartTrialView
from payments.views import CheckoutInitiationView, MercadoPagoWebhookView
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
    # The non-read-only endpoints under /api/. contacto is anonymous (see
    # ContactMessageCreateView's docstring for why that's safe without
    # session auth/CSRF); checkout is authenticated-only (see
    # CheckoutInitiationView's docstring) and keeps SessionAuthentication —
    # and therefore CSRF — fully enabled. mercadopago/webhook/ is also
    # anonymous (same authentication_classes = [] reasoning as contacto)
    # but is NOT low-stakes like contacto — see MercadoPagoWebhookView's
    # docstring for how it establishes trust cryptographically instead.
    path('contacto/', ContactMessageCreateView.as_view(), name='contact-message-create'),
    path('checkout/', CheckoutInitiationView.as_view(), name='checkout-initiate'),
    path('mercadopago/webhook/', MercadoPagoWebhookView.as_view(), name='mercadopago-webhook'),
    # Authenticated-only, same CSRF/SessionAuthentication reasoning as
    # checkout — see StartTrialView's own docstring.
    path('trial/', StartTrialView.as_view(), name='start-trial'),
    path('', include(router.urls)),
]
