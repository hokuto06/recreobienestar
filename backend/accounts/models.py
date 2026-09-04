from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class Profile(TimeStampedModel):
    """One per User, auto-created via signal (see signals.py) so every user
    — including the superuser — always has one without extra setup steps.

    `email` is deliberately NOT a stored column: User.email is the single
    source of truth and duplicating it here would let the two drift apart.
    The `email` property below is what satisfies "profile containing ...
    email" — templates/API code can read `profile.email` either way.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile',
    )
    display_name = models.CharField(
        max_length=150, blank=True,
        help_text='Nombre a mostrar. Si se deja vacío, se usa el nombre de usuario.',
    )
    avatar_url = models.URLField(blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)

    class Meta:
        verbose_name = 'Perfil'
        verbose_name_plural = 'Perfiles'

    def __str__(self):
        return self.display_name or self.user.get_username()

    @property
    def email(self):
        return self.user.email

    @property
    def avatar_display_url(self):
        if self.avatar:
            return self.avatar.url
        return self.avatar_url or ''

    @property
    def name_for_display(self):
        return self.display_name or self.user.get_username()
