from rest_framework import generics, viewsets
from rest_framework.response import Response

from memberships.services import can_access_video

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

    def get_serializer_context(self):
        """Adds `subscriptions`: the caller's subscriptions, fetched once
        per request (not once per video) — VideoListSerializer.get_thumbnail
        passes this straight through to can_access_video. Without it, a
        paginated list of 20 videos would run 20 separate subscription
        queries instead of this single one. Only needed for `list` —
        `retrieve` handles a single video and already resolved access in
        retrieve() below before the serializer even runs."""
        context = super().get_serializer_context()
        if self.action == 'list':
            request = context.get('request')
            user = getattr(request, 'user', None) if request else None
            context['subscriptions'] = (
                list(user.subscriptions.select_related('plan'))
                if user is not None and user.is_authenticated else []
            )
        return context

    def retrieve(self, request, *args, **kwargs):
        """Mirrors catalog.public_views.video_detail's rule exactly: check
        access BEFORE building a response that would include
        youtube_video_id, so a locked video's ID never reaches this
        endpoint's output at all — not redacted, not present."""
        instance = self.get_object()
        if not can_access_video(request.user, instance):
            return Response(
                {'detail': 'No tenés acceso a este video con tu membresía actual.'},
                status=403,
            )
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
