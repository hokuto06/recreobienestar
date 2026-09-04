from rest_framework import generics

from .models import MembershipPlan
from .serializers import MembershipPlanSerializer


class MembershipPlanListView(generics.ListAPIView):
    """GET /api/plans/ — active plans only."""
    serializer_class = MembershipPlanSerializer
    queryset = MembershipPlan.objects.filter(is_active=True).order_by('display_order', 'name')
