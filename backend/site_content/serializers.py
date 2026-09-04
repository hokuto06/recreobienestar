from rest_framework import serializers

from .models import ContactMessage, Offering, SiteSettings, Testimonial


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


class TestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Testimonial
        # is_active deliberately excluded: internal visibility toggle,
        # not marketing content — mirrors why Offering doesn't expose it.
        fields = ['id', 'author_name', 'text', 'rating', 'display_order']


class ContactMessageSerializer(serializers.ModelSerializer):
    """POST /api/contacto/ input. is_read is deliberately not a field here
    at all (not even read_only) — it's an admin-only toggle, never set by
    the submitter, mirroring why Testimonial/Offering never expose their
    own internal-only flags.

    `website` is a honeypot, not a model field: a real visitor never sees
    it (hidden off-screen in CSS — see .field--honeypot in style.css), so
    any value here means a bot filled every input it could find. Declared
    write_only/not required so its absence never fails validation for a
    real submission; the view checks it after is_valid() and silently
    drops anything that filled it in, without saving or revealing the
    rejection to the caller.
    """
    website = serializers.CharField(required=False, allow_blank=True, write_only=True, max_length=200)

    class Meta:
        model = ContactMessage
        fields = ['name', 'email', 'message', 'website']

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Ingresá tu nombre.')
        return value

    def validate_message(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Ingresá tu mensaje.')
        return value
