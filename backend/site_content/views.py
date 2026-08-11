from rest_framework import generics

from .models import Offering, SiteSettings
from .serializers import OfferingSerializer, SiteSettingsSerializer


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
