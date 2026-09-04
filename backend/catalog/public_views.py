"""
Public, template-rendered views — the video library, video detail, and
program pages, plus the two small POST-only engagement endpoints
(favorite toggle, mark-completed). Kept separate from views.py (the JSON
API viewsets) since these serve HTML to browsers, not the REST API.
"""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from memberships.services import can_access_video

from .models import Category, Program, Video
from .services import (
    get_continue_watching,
    get_favorited_video_ids,
    get_progress_map,
    record_video_view,
    toggle_favorite,
)


def _subscriptions_for(user):
    """One query for the page's worth of access checks, instead of one per
    video — see memberships/services.py's module docstring."""
    if not getattr(user, 'is_authenticated', False):
        return []
    return list(user.subscriptions.select_related('plan'))


def _stamp_engagement(user, videos):
    """Stamps .unlocked, .is_favorited, .progress on each video in `videos`
    (a list, so this can iterate it twice) — one query per concern, never
    one per video. Mirrors the existing .unlocked pattern used throughout
    this app (see module docstrings in memberships/services.py)."""
    subscriptions = _subscriptions_for(user)
    favorited_ids = get_favorited_video_ids(user, videos=videos)
    progress_map = get_progress_map(user, videos=videos)
    for video in videos:
        video.unlocked = can_access_video(user, video, subscriptions=subscriptions)
        video.is_favorited = video.id in favorited_ids
        video.progress = progress_map.get(video.id)
    return videos


PAGE_SIZE = 12


def video_library(request):
    """GET /videoteca/ — published videos, optionally filtered by
    ?category=<slug>, ?program=<slug>, and/or sorted by ?sort=newest,
    paginated. Locked/unlocked state is computed once here (via
    can_access_video) and never re-derived in the template."""
    videos = (
        Video.objects.filter(is_published=True)
        .select_related('category', 'program')
    )

    sort = request.GET.get('sort', '')
    if sort == 'newest':
        videos = videos.order_by('-publication_date', 'display_order')
    else:
        videos = videos.order_by('display_order', '-publication_date')

    categories = Category.objects.filter(is_active=True).order_by('display_order', 'name')
    programs = Program.objects.filter(is_active=True).order_by('display_order', 'name')

    active_category = request.GET.get('category', '')
    active_program = request.GET.get('program', '')
    if active_category:
        videos = videos.filter(category__slug=active_category)
    if active_program:
        videos = videos.filter(program__slug=active_program)

    # Featured strip: only shown on the unfiltered, first-page, default-sort
    # view — a curated highlight, not another way to slice the same list.
    show_featured = not active_category and not active_program and not sort and request.GET.get('page', '1') == '1'
    featured_videos = []
    if show_featured:
        featured_videos = list(
            Video.objects.filter(is_published=True, is_featured=True)
            .select_related('category', 'program')
            .order_by('display_order')[:4]
        )

    paginator = Paginator(videos, PAGE_SIZE)
    page_number = request.GET.get('page')
    page = paginator.get_page(page_number)

    _stamp_engagement(request.user, list(page.object_list) + featured_videos)

    # Preserve the active filters across pagination/sort links.
    querystring = request.GET.copy()
    querystring.pop('page', None)

    context = {
        'page': page,
        'categories': categories,
        'programs': programs,
        'active_category': active_category,
        'active_program': active_program,
        'active_sort': sort,
        'featured_videos': featured_videos,
        'querystring': querystring.urlencode(),
    }
    return render(request, 'catalog/video_library.html', context)


def _related_videos(video, limit=4):
    """Same program first (excluding this video), topped up with same-
    category videos if the program doesn't have enough — always published,
    never re-including the video itself or a duplicate."""
    seen_ids = {video.id}
    related = []

    if video.program_id:
        for candidate in (
            Video.objects.filter(program=video.program, is_published=True)
            .exclude(id=video.id)
            .select_related('category', 'program')
            .order_by('display_order')[:limit]
        ):
            related.append(candidate)
            seen_ids.add(candidate.id)

    if len(related) < limit:
        remaining = limit - len(related)
        for candidate in (
            Video.objects.filter(category=video.category, is_published=True)
            .exclude(id__in=seen_ids)
            .select_related('category', 'program')
            .order_by('display_order')[:remaining]
        ):
            related.append(candidate)

    return related


def _program_neighbors(video):
    """(previous, next) videos within the same program, by display_order —
    None/None if the video isn't part of a program. Only considers
    published videos, same as every other public listing in this app."""
    if not video.program_id:
        return None, None
    siblings = list(
        Video.objects.filter(program=video.program, is_published=True)
        .order_by('display_order', 'id')
        .values_list('id', 'slug', 'title')
    )
    ids = [s[0] for s in siblings]
    try:
        index = ids.index(video.id)
    except ValueError:
        # The video itself isn't published (e.g. staff previewing a draft)
        # — no well-defined position among its published siblings.
        return None, None
    previous = siblings[index - 1] if index > 0 else None
    nxt = siblings[index + 1] if index < len(siblings) - 1 else None
    return previous, nxt


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

    progress = record_video_view(request.user, video)
    is_favorited = video.id in get_favorited_video_ids(request.user, videos=[video])

    related = _related_videos(video)
    _stamp_engagement(request.user, related)
    previous_video, next_video = _program_neighbors(video)

    context = {
        'video': video,
        'is_favorited': is_favorited,
        'progress': progress,
        'related_videos': related,
        'previous_video': previous_video,
        'next_video': next_video,
    }
    return render(request, 'catalog/video_detail.html', context)


@login_required
@require_POST
def mark_video_completed(request, slug):
    """POST /videos/<slug>/completado/ — marks the video as completed for
    the current member. Requires the same access as watching it at all;
    a plain full-page redirect back to the detail page is enough here (no
    JS requirement was asked for progress, unlike favorites)."""
    video = get_object_or_404(Video, slug=slug)
    if not can_access_video(request.user, video):
        raise Http404
    progress = record_video_view(request.user, video)
    progress.mark_completed()
    return redirect('catalog:video_detail', slug=slug)


@login_required
@require_POST
def toggle_favorite_view(request, slug):
    """POST /videos/<slug>/favorito/ — add/remove a favorite, no page
    reload when called via fetch() (see static/site/js/site.js), with a
    plain-redirect fallback for a non-JS form submit. CSRF is enforced
    exactly like every other POST in this app — the fetch call sends the
    token read from the <meta name="csrf-token"> tag in base.html (see
    settings.CSRF_COOKIE_HTTPONLY's docstring for why it's not read from
    the cookie instead)."""
    video = get_object_or_404(Video, slug=slug)
    is_favorited = toggle_favorite(request.user, video)
    if request.headers.get('Accept') == 'application/json':
        return JsonResponse({'favorited': is_favorited, 'video': video.slug})
    return redirect(request.POST.get('next') or 'catalog:video_detail', slug=slug)


def program_detail(request, slug):
    """GET /programas/<slug>/ — a program's own page: description, its
    published videos in curatorial order, and (for members) a small
    progress summary. Inactive programs 404 for everyone, same as
    Category elsewhere in this app — no staff-preview bypass exists for
    Program (unlike Video, nothing today needs to preview a draft program)."""
    program = get_object_or_404(Program, slug=slug, is_active=True)
    videos = list(
        Video.objects.filter(program=program, is_published=True)
        .select_related('category', 'program')
        .order_by('display_order', 'id')
    )
    _stamp_engagement(request.user, videos)

    progress_summary = None
    if request.user.is_authenticated and videos:
        completed_count = sum(1 for v in videos if v.progress and v.progress.completed)
        progress_summary = {'completed': completed_count, 'total': len(videos)}

    context = {
        'program': program,
        'videos': videos,
        'progress_summary': progress_summary,
    }
    return render(request, 'catalog/program_detail.html', context)
