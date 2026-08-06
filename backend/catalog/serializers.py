"""
Public read-only API representations.

Deliberately hand-picked field lists rather than `fields = '__all__'`: the
whole point is to never leak internal-only columns (is_active/is_published
— redundant anyway since the querysets already filter on them —
created_at/updated_at, raw foreign key ids) through the public API.
"""
from rest_framework import serializers

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

    class Meta:
        model = Video
        fields = [
            'id', 'title', 'slug', 'short_description', 'thumbnail',
            'category', 'program', 'access_level', 'is_featured',
            'display_order', 'duration_label', 'publication_date',
        ]

    def get_thumbnail(self, obj):
        if obj.thumbnail_url:
            return obj.thumbnail_url
        if obj.youtube_video_id:
            return f'https://img.youtube.com/vi/{obj.youtube_video_id}/hqdefault.jpg'
        return None


class VideoDetailSerializer(VideoListSerializer):
    """Adds the fields only needed once you're actually viewing one video —
    full_description and the id used to embed the player. Raw youtube_url
    is intentionally not exposed; youtube_video_id is all a player needs."""
    class Meta(VideoListSerializer.Meta):
        fields = VideoListSerializer.Meta.fields + ['full_description', 'youtube_video_id']
