from rest_framework import serializers

from .models import MembershipPlan


class MembershipPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = MembershipPlan
        fields = [
            'id', 'tier', 'name', 'subtitle', 'slug', 'description',
            'badge', 'visual_variant', 'cta_label', 'price',
            'currency', 'duration_days', 'display_order',
        ]
