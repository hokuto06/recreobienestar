from rest_framework import generics, viewsets

from .filters import VideoFilter
from .models import Category, Program, Video
from .serializers import (
    CategorySerializer,
    ProgramSerializer,
    VideoDetailSerializer,
    VideoListSerializer,
)


class CategoryListView(generics.ListAPIView):
    """GET /api/categories/ — active categories only. List-only: no POST
    handler exists on ListAPIView, so writes get a 405."""
    serializer_class = CategorySerializer
    queryset = Category.objects.filter(is_active=True).order_by('display_order', 'name')


class ProgramListView(generics.ListAPIView):
    """GET /api/programs/ — active programs only."""
    serializer_class = ProgramSerializer
    queryset = Program.objects.filter(is_active=True).order_by('display_order', 'name')


class VideoViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/videos/ and GET /api/videos/<slug>/ — published videos only.

    ReadOnlyModelViewSet only wires up list/retrieve (no create/update/
    delete handlers exist at all), so POST/PUT/PATCH/DELETE are never
    routed and return 405 regardless of who's asking — there is no public
    write path to lock down because none was ever built.
    """
    lookup_field = 'slug'
    filterset_class = VideoFilter
    queryset = (
        Video.objects.filter(is_published=True)
        .select_related('category', 'program')
        .order_by('display_order', '-publication_date')
    )

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return VideoDetailSerializer
        return VideoListSerializer
