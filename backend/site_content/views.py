from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import ContactMessage, Offering, SiteSettings, Testimonial
from .serializers import (
    ContactMessageSerializer, OfferingSerializer, SiteSettingsSerializer, TestimonialSerializer,
)


class SiteSettingsView(generics.RetrieveAPIView):
    """GET /api/site-settings/ — the one row (created lazily if it doesn't
    exist yet — see SiteSettings.load()). No pk in the URL: there's only
    ever one."""
    serializer_class = SiteSettingsSerializer

    def get_object(self):
        return SiteSettings.load()


class OfferingListView(generics.ListAPIView):
    """GET /api/offerings/ — active one-time-purchase offerings only."""
    serializer_class = OfferingSerializer
    queryset = Offering.objects.filter(is_active=True).order_by('display_order', 'name')


class TestimonialListView(generics.ListAPIView):
    """GET /api/testimonials/ — active testimonials only, for the home
    page carousel."""
    serializer_class = TestimonialSerializer
    queryset = Testimonial.objects.filter(is_active=True).order_by('display_order', 'id')


class ContactMessageCreateView(APIView):
    """POST /api/contacto/ — the public contact form (Fase 3.9), the first
    write endpoint under /api/. Deliberately anonymous by design rather
    than session-authenticated:

    - `authentication_classes = []` means this view never resolves
      request.user via a session, so it's never treated as an
      authenticated request — DRF's SessionAuthentication only runs its
      CSRF check when it DOES identify a session user (see
      config.settings.REST_FRAMEWORK's own comment on why VideoViewSet
      still needs SessionAuthentication; this view is the opposite case:
      it doesn't care who's asking, logged in or not). DRF views are
      already csrf_exempt at the Django-middleware level by design, so
      skipping SessionAuthentication here is what actually matters.
    - This is a per-view choice, not a global one: no CSRF/CORS/cookie
      setting changed, and every other endpoint keeps the project
      default (SessionAuthentication + AllowAny).
    - Abuse is handled by input validation (serializer), a honeypot field
      (see ContactMessageSerializer), and a per-IP throttle scoped only
      to this view (see DEFAULT_THROTTLE_RATES['contact'] in settings.py)
      — not by weakening any protection elsewhere.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'contact'

    def post(self, request):
        serializer = ContactMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Honeypot tripped: respond exactly like a real success (same
        # status/body) but never persist. No visible sign of the
        # rejection — that's what keeps a bot from learning to avoid it.
        if serializer.validated_data.get('website'):
            return Response({'detail': 'Mensaje recibido.'}, status=status.HTTP_201_CREATED)

        ContactMessage.objects.create(
            name=serializer.validated_data['name'],
            email=serializer.validated_data['email'],
            message=serializer.validated_data['message'],
        )
        return Response({'detail': 'Mensaje recibido.'}, status=status.HTTP_201_CREATED)
