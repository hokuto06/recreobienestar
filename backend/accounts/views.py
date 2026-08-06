from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView

from catalog.models import Video
from common.choices import VideoAccessLevel
from memberships.models import Subscription
from memberships.services import can_access_video

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


class ProfileEditView(LoginRequiredMixin, UpdateView):
    """GET/POST /mi-cuenta/perfil/"""
    login_url = reverse_lazy('accounts:login')
    form_class = ProfileForm
    template_name = 'accounts/profile_edit.html'
    success_url = reverse_lazy('accounts:dashboard')

    def get_object(self, queryset=None):
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        return profile

    def form_valid(self, form):
        messages.success(self.request, 'Tu perfil fue actualizado.')
        return super().form_valid(form)


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
    current_subscription = max(all_subscriptions, key=lambda s: s.created_at, default=None)
    membership_is_active = current_subscription.is_active() if current_subscription else False

    published_videos = list(
        Video.objects.filter(is_published=True).select_related('category', 'program')
    )
    # Computed once, here, and stamped onto each instance — templates read
    # video.unlocked rather than re-deriving access (Django's template
    # language can't express `video in available_videos` in a {% with %},
    # and re-checking per-template would risk drifting from this decision).
    for video in published_videos:
        video.unlocked = can_access_video(user, video, subscriptions=all_subscriptions)
    available_videos = [v for v in published_videos if v.unlocked]
    locked_videos = [v for v in published_videos if not v.unlocked]

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
    }
    return render(request, 'accounts/dashboard.html', context)
