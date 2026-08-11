from rest_framework import serializers

from .models import Offering, SiteSettings


class SiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        fields = [
            'tagline', 'hero_headline', 'carla_bio', 'carla_bio_highlight',
            'contact_email', 'instagram_url',
            'podcast_name', 'podcast_url',
        ]


class OfferingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Offering
        fields = [
            'id', 'name', 'slug', 'description', 'price', 'currency',
            'payment_url_ars', 'payment_url_usd', 'display_order',
        ]
