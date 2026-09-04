"""
Progress and favorites domain logic — the counterpart to
memberships/services.py for the Phase 3 engagement features.

Same performance discipline as memberships.services: every batch lookup
takes the page's videos and returns a dict/set keyed by video id, so a
library/dashboard page issues one query for "what's favorited" and one for
"what's in progress" — never one per video.
"""
from django.utils import timezone

from .models import Favorite, VideoProgress


def get_favorited_video_ids(user, videos=None):
    """Set of video ids `user` has favorited. Pass `videos` (an iterable of
    Video instances/ids) to scope the query to just the videos on the
    current page — without it, every favorite the user has ever made is
    fetched, which is still one query but needlessly large for a user with
    a long history."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    qs = Favorite.objects.filter(user=user)
    if videos is not None:
        video_ids = [v.id if hasattr(v, 'id') else v for v in videos]
        qs = qs.filter(video_id__in=video_ids)
    return set(qs.values_list('video_id', flat=True))


def get_progress_map(user, videos=None):
    """Dict of {video_id: VideoProgress} for `user`. Same batching rationale
    as get_favorited_video_ids."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return {}
    qs = VideoProgress.objects.filter(user=user)
    if videos is not None:
        video_ids = [v.id if hasattr(v, 'id') else v for v in videos]
        qs = qs.filter(video_id__in=video_ids)
    return {p.video_id: p for p in qs}


def get_continue_watching(user, limit=6):
    """Videos `user` started but hasn't completed, most recently viewed
    first — the "Continuar viendo" dashboard section. Excludes videos that
    were unpublished after the member started them (nothing to resume)."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    return list(
        VideoProgress.objects.filter(user=user, completed=False, video__is_published=True)
        .select_related('video', 'video__category', 'video__program')
        .order_by('-last_viewed_at')[:limit]
    )


def record_video_view(user, video):
    """Call once, server-side, when an authenticated member is actually
    granted access to a video's detail page (i.e. AFTER can_access_video
    already returned True) — creates the progress row on first view, just
    bumps last_viewed_at on every view after that. Never called for
    anonymous visitors (there's no user to attach the row to) or for
    locked videos (nothing was actually watched)."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    progress, created = VideoProgress.objects.get_or_create(
        user=user, video=video,
        defaults={'started_at': timezone.now(), 'last_viewed_at': timezone.now()},
    )
    if not created:
        progress.last_viewed_at = timezone.now()
        progress.save(update_fields=['last_viewed_at', 'updated_at'])
    return progress


def toggle_favorite(user, video):
    """Returns True if `video` is now favorited, False if it was just
    removed. Bookmarking doesn't require access to the video (see
    Favorite's docstring) — only that the caller is authenticated, which
    the calling view enforces."""
    favorite = Favorite.objects.filter(user=user, video=video).first()
    if favorite:
        favorite.delete()
        return False
    Favorite.objects.create(user=user, video=video)
    return True
