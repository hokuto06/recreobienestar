from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordChangeDoneView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from catalog.models import Favorite, Video
from catalog.services import get_continue_watching, get_favorited_video_ids, get_progress_map
from common.choices import VideoAccessLevel
from memberships.models import Subscription
from memberships.services import can_access_video, get_current_subscription

from .forms import EmailOrUsernameAuthenticationForm, ProfileForm, RegistrationForm
from .models import Profile


class RegisterView(CreateView):
    """GET/POST /registro/ — email + password registration. Logs the new
    member in immediately and drops them on the dashboard, per spec (no
    separate "verify your email" step in this phase)."""
    form_class = RegistrationForm
    template_name = 'accounts/register.html'
    success_url = reverse_lazy('accounts:dashboard')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        auth_login(self.request, self.object, backend='accounts.backends.EmailOrUsernameModelBackend')
        messages.success(self.request, f'¡Bienvenida, {self.object.profile.name_for_display}! Tu cuenta fue creada.')
        return response


class MemberLoginView(LoginView):
    """GET/POST /ingresar/ — accepts username OR email (see
    accounts.backends.EmailOrUsernameModelBackend).

    No get_success_url() override here on purpose: LoginView's own default
    already does the right thing — redirect to a safe `?next=` if one was
    given (e.g. a protected page bounced you here), otherwise
    settings.LOGIN_REDIRECT_URL ('/mi-cuenta/'). An earlier version of this
    view overrode it to always go to the dashboard, which silently dropped
    `next` and sent people somewhere other than where they were headed."""
    template_name = 'accounts/login.html'
    authentication_form = EmailOrUsernameAuthenticationForm
    redirect_authenticated_user = True


class MemberLogoutView(LogoutView):
    """POST /salir/ — explicitly POST-only (Django 4.2's LogoutView still
    accepts GET by default; restricting http_method_names here rather than
    relying on that changing in a future Django upgrade). The nav renders
    this as a small form with a CSRF token, not a plain link, so logging
    someone out can't be triggered by a bare cross-site GET.

    next_page is the raw site root, not a reverse() — the landing page at
    '/' is the existing static site served directly by nginx, not a Django
    view, so there's no URL name to point at."""
    next_page = '/'
    http_method_names = ['post', 'options']


class MemberPasswordResetView(PasswordResetView):
    template_name = 'accounts/password_reset_form.html'
    email_template_name = 'accounts/password_reset_email.html'
    subject_template_name = 'accounts/password_reset_subject.txt'
    success_url = reverse_lazy('accounts:password_reset_done')


class MemberPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'accounts/password_reset_done.html'


class MemberPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('accounts:password_reset_complete')


class MemberPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'accounts/password_reset_complete.html'


class MemberPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    """GET/POST /mi-cuenta/cambiar-clave/ — requires the current password
    (unlike the anonymous /recuperar-clave/ flow, which is for members
    who've lost access entirely). Keeps the member logged in afterward —
    PasswordChangeView already updates the session auth hash so the
    current session isn't invalidated by the password change."""
    login_url = reverse_lazy('accounts:login')
    template_name = 'accounts/password_change_form.html'
    success_url = reverse_lazy('accounts:password_change_done')


class MemberPasswordChangeDoneView(LoginRequiredMixin, PasswordChangeDoneView):
    login_url = reverse_lazy('accounts:login')
    template_name = 'accounts/password_change_done.html'


class ProfileEditView(LoginRequiredMixin, UpdateView):
    """GET/POST /mi-cuenta/perfil/ — the edit form, plus (added in Phase 3)
    a read-only account summary above it: display name, email, membership
    status, and account creation date. No new personal fields were added —
    everything shown already existed on User/Profile/Subscription."""
    login_url = reverse_lazy('accounts:login')
    form_class = ProfileForm
    template_name = 'accounts/profile_edit.html'
    success_url = reverse_lazy('accounts:dashboard')

    def get_object(self, queryset=None):
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        return profile

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['subscription'] = get_current_subscription(self.request.user)
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Tu perfil fue actualizado.')
        return super().form_valid(form)


class FavoritesListView(LoginRequiredMixin, ListView):
    """GET /mi-cuenta/favoritos/ — every video the member has favorited,
    most recent first. Locked/unlocked is still computed per-video (a
    favorited video's access can change over time, e.g. a membership
    lapsed), never assumed from having favorited it."""
    login_url = reverse_lazy('accounts:login')
    template_name = 'accounts/favorites.html'
    context_object_name = 'favorites'
    paginate_by = 12

    def get_queryset(self):
        return (
            Favorite.objects.filter(user=self.request.user)
            .select_related('video', 'video__category', 'video__program')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        videos = [f.video for f in context['favorites']]
        subscriptions = list(self.request.user.subscriptions.select_related('plan'))
        for video in videos:
            video.unlocked = can_access_video(self.request.user, video, subscriptions=subscriptions)
            video.is_favorited = True
        return context


@login_required(login_url=reverse_lazy('accounts:login'))
def dashboard(request):
    """GET /mi-cuenta/ — the member dashboard. All access decisions here go
    through memberships.services.can_access_video — nothing here
    re-derives who's allowed to watch what."""
    user = request.user
    profile, _ = Profile.objects.get_or_create(user=user)

    # Fetched once and reused for both the "current plan" display and every
    # can_access_video() check below — without passing this list through,
    # each video would re-query the user's subscriptions from scratch
    # (an N+1: one query per video instead of this single one).
    all_subscriptions = list(Subscription.objects.filter(user=user).select_related('plan'))
    current_subscription = get_current_subscription(user, subscriptions=all_subscriptions)
    membership_is_active = current_subscription.is_active() if current_subscription else False

    published_videos = list(
        Video.objects.filter(is_published=True).select_related('category', 'program')
    )
    # Same batching discipline for favorites/progress as for access — one
    # query each for the whole dashboard, not one per video (see
    # catalog/services.py).
    favorited_ids = get_favorited_video_ids(user, videos=published_videos)
    progress_map = get_progress_map(user, videos=published_videos)
    # Computed once, here, and stamped onto each instance — templates read
    # video.unlocked rather than re-deriving access (Django's template
    # language can't express `video in available_videos` in a {% with %},
    # and re-checking per-template would risk drifting from this decision).
    for video in published_videos:
        video.unlocked = can_access_video(user, video, subscriptions=all_subscriptions)
        video.is_favorited = video.id in favorited_ids
        video.progress = progress_map.get(video.id)
    available_videos = [v for v in published_videos if v.unlocked]
    locked_videos = [v for v in published_videos if not v.unlocked]
    completed_count = sum(1 for p in progress_map.values() if p.completed)

    # "Continue watching" videos were accessible when the member started
    # them, but access is re-checked here rather than assumed — a lapsed
    # subscription must re-lock the card (and its thumbnail) exactly like
    # everywhere else, not just skip straight to "still available".
    continue_watching = get_continue_watching(user, limit=6)
    for progress in continue_watching:
        progress.video.unlocked = can_access_video(user, progress.video, subscriptions=all_subscriptions)
        progress.video.is_favorited = progress.video.id in favorited_ids
        progress.video.progress = progress

    context = {
        'profile': profile,
        'subscription': current_subscription,
        'membership_is_active': membership_is_active,
        'available_videos': sorted(available_videos, key=lambda v: v.display_order)[:8],
        'locked_videos': sorted(locked_videos, key=lambda v: v.display_order)[:6],
        'recent_videos': sorted(
            [v for v in published_videos if v.publication_date],
            key=lambda v: v.publication_date, reverse=True,
        )[:4],
        'featured_videos': [v for v in published_videos if v.is_featured][:4],
        'free_videos': [v for v in published_videos if v.access_level == VideoAccessLevel.FREE][:4],
        'continue_watching': continue_watching,
        'favorite_videos': [v for v in published_videos if v.is_favorited][:6],
        'completed_count': completed_count,
    }
    return render(request, 'accounts/dashboard.html', context)
