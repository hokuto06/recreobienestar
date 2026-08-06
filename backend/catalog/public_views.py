"""
Public, template-rendered views — the video library and video detail
pages. Kept separate from views.py (the JSON API viewsets) since these
serve HTML to browsers, not the REST API.
"""
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from memberships.services import can_access_video

from .models import Category, Program, Video


def _subscriptions_for(user):
    """One query for the page's worth of access checks, instead of one per
    video — see memberships/services.py's module docstring."""
    if not getattr(user, 'is_authenticated', False):
        return []
    return list(user.subscriptions.select_related('plan'))

PAGE_SIZE = 12


def video_library(request):
    """GET /videoteca/ — published videos, optionally filtered by
    ?category=<slug> and/or ?program=<slug>, paginated. Locked/unlocked
    state is computed once here (via can_access_video) and never
    re-derived in the template."""
    videos = (
        Video.objects.filter(is_published=True)
        .select_related('category', 'program')
        .order_by('display_order', '-publication_date')
    )

    categories = Category.objects.filter(is_active=True).order_by('display_order', 'name')
    programs = Program.objects.filter(is_active=True).order_by('display_order', 'name')

    active_category = request.GET.get('category', '')
    active_program = request.GET.get('program', '')
    if active_category:
        videos = videos.filter(category__slug=active_category)
    if active_program:
        videos = videos.filter(program__slug=active_program)

    paginator = Paginator(videos, PAGE_SIZE)
    page_number = request.GET.get('page')
    page = paginator.get_page(page_number)

    subscriptions = _subscriptions_for(request.user)
    for video in page.object_list:
        video.unlocked = can_access_video(request.user, video, subscriptions=subscriptions)

    # Preserve the active filters across pagination links.
    querystring = request.GET.copy()
    querystring.pop('page', None)

    context = {
        'page': page,
        'categories': categories,
        'programs': programs,
        'active_category': active_category,
        'active_program': active_program,
        'querystring': querystring.urlencode(),
    }
    return render(request, 'catalog/video_library.html', context)


def video_detail(request, slug):
    """GET /videos/<slug>/ — access is checked server-side BEFORE any
    YouTube field is placed in the template context. The locked template
    never receives youtube_video_id/youtube_url at all, so there is
    nothing for it to leak.

    Deliberately does NOT filter the queryset by is_published: that
    decision belongs to can_access_video alone (it denies unpublished
    videos to everyone except staff/superusers, who can preview drafts —
    see memberships/services.py). Filtering here too would 404 staff
    before they ever reach that bypass.
    """
    video = get_object_or_404(Video.objects.select_related('category', 'program'), slug=slug)

    if not can_access_video(request.user, video):
        return render(request, 'catalog/video_locked.html', {'video': video}, status=403)

    return render(request, 'catalog/video_detail.html', {'video': video})
