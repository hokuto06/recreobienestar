"""
Public read-only API representations.

Deliberately hand-picked field lists rather than `fields = '__all__'`: the
whole point is to never leak internal-only columns (is_active/is_published
— redundant anyway since the querysets already filter on them —
created_at/updated_at, raw foreign key ids) through the public API.

Locked-video fields (thumbnail, full_description, youtube_video_id) go
through memberships.services.can_access_video exactly like the HTML
views — this is not a separate copy of the access rules, just the same
check applied at the API boundary too. See VideoViewSet.retrieve() for the
detail-endpoint half of this.
"""
from rest_framework import serializers

from memberships.services import can_access_video

from .models import Category, Program, Video


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'display_order']


class CategoryMiniSerializer(serializers.ModelSerializer):
    """Nested inside VideoSerializer — just enough to link/label, not the
    full category payload."""
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug']


class ProgramSerializer(serializers.ModelSerializer):
    cover_image = serializers.CharField(source='cover_image_display_url', read_only=True)

    class Meta:
        model = Program
        fields = ['id', 'name', 'slug', 'description', 'cover_image', 'display_order']


class ProgramMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Program
        fields = ['id', 'name', 'slug']


class VideoListSerializer(serializers.ModelSerializer):
    category = CategoryMiniSerializer(read_only=True)
    program = ProgramMiniSerializer(read_only=True)
    thumbnail = serializers.SerializerMethodField()
    # Human-readable label ("Gratuito", "Plan de membresía 1", ...) for
    # clients that just want to display it without duplicating
    # common.choices.VideoAccessLevel's labels themselves (e.g. the public
    # home page's vanilla-JS fetch — see nginx/static-root/js/home-dynamic.js).
    access_level_display = serializers.CharField(source='get_access_level_display', read_only=True)

    class Meta:
        model = Video
        fields = [
            'id', 'title', 'slug', 'short_description', 'thumbnail',
            'category', 'program', 'access_level', 'access_level_display',
            'is_featured', 'display_order', 'duration_label', 'publication_date',
        ]

    def get_thumbnail(self, obj):
        # The thumbnail fallback (Video.thumbnail_display_url) derives a
        # img.youtube.com URL FROM the video ID when no explicit
        # thumbnail_url is set — showing it for a video the caller can't
        # access would hand them the ID just as surely as the detail
        # endpoint's youtube_video_id field would. Same check, same rule.
        #
        # `subscriptions` (see VideoViewSet.get_serializer_context) is the
        # caller's subscriptions fetched once for the whole page, not
        # re-queried for every video in the list.
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        subscriptions = self.context.get('subscriptions')
        if not can_access_video(user, obj, subscriptions=subscriptions):
            return None
        return obj.thumbnail_display_url or None


class VideoDetailSerializer(VideoListSerializer):
    """Adds the fields only needed once you're actually viewing one video —
    full_description and the id used to embed the player. Raw youtube_url
    is intentionally not exposed; youtube_video_id is all a player needs.

    NOTE: this serializer assumes access was already checked — see
    VideoViewSet.retrieve(), which returns 403 before this ever runs for a
    video the caller can't access. It does not re-check here itself."""
    class Meta(VideoListSerializer.Meta):
        fields = VideoListSerializer.Meta.fields + ['full_description', 'youtube_video_id']
