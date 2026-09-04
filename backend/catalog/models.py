from django.conf import settings
from django.db import models
from django.utils import timezone

from common.choices import VideoAccessLevel
from common.models import OrderedActiveModel, TimeStampedModel
from common.text import extract_youtube_id, generate_unique_slug
from common.validators import validate_youtube_url


class Category(OrderedActiveModel, TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    description = models.TextField(blank=True)

    class Meta(OrderedActiveModel.Meta):
        verbose_name = 'Categoría'
        verbose_name_plural = 'Categorías'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, self.name)
        super().save(*args, **kwargs)


class Program(OrderedActiveModel, TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    description = models.TextField(blank=True)

    # Either a hosted URL or an uploaded file — whichever is easier for
    # Carla for a given program. Neither is required.
    cover_image_url = models.URLField(blank=True)
    cover_image = models.ImageField(upload_to='programs/covers/', blank=True, null=True)

    class Meta(OrderedActiveModel.Meta):
        verbose_name = 'Programa'
        verbose_name_plural = 'Programas'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, self.name)
        super().save(*args, **kwargs)

    @property
    def cover_image_display_url(self):
        if self.cover_image:
            return self.cover_image.url
        return self.cover_image_url or ''


class Video(TimeStampedModel):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)

    short_description = models.CharField(
        max_length=300, blank=True,
        help_text='Resumen breve para listados y tarjetas.',
    )
    full_description = models.TextField(blank=True)

    youtube_url = models.URLField(
        max_length=500,
        validators=[validate_youtube_url],
        help_text='Enlace completo de YouTube (watch, youtu.be, embed o shorts).',
    )
    youtube_video_id = models.CharField(
        max_length=20, blank=True, editable=False,
        help_text='Extraído automáticamente de youtube_url.',
    )
    thumbnail_url = models.URLField(
        max_length=500, blank=True,
        help_text='Opcional. Si se deja vacío, puede usarse la miniatura de YouTube.',
    )

    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name='videos',
    )
    program = models.ForeignKey(
        Program, on_delete=models.SET_NULL, related_name='videos',
        null=True, blank=True,
    )

    access_level = models.CharField(
        max_length=20,
        choices=VideoAccessLevel.choices,
        default=VideoAccessLevel.FREE,
        help_text='Nivel de membresía requerido para ver este video.',
    )

    is_published = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0, help_text='Menor valor aparece primero.')
    duration_label = models.CharField(
        max_length=20, blank=True,
        help_text='Ej: "12:34". Texto libre, no se calcula automáticamente.',
    )
    publication_date = models.DateTimeField(
        null=True, blank=True,
        help_text='Fecha de publicación (puede programarse a futuro).',
    )

    class Meta:
        ordering = ['display_order', '-publication_date', 'id']
        verbose_name = 'Video'
        verbose_name_plural = 'Videos'
        # is_published is in the WHERE clause of nearly every query in the
        # app (library, dashboard, API, detail access check) — worth an
        # explicit index even though today's catalog is small.
        indexes = [models.Index(fields=['is_published'])]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, self.title)
        self.youtube_video_id = extract_youtube_id(self.youtube_url) or ''
        super().save(*args, **kwargs)

    @property
    def is_free(self):
        return self.access_level == VideoAccessLevel.FREE

    @property
    def thumbnail_display_url(self):
        """Shared by templates and the API serializer (catalog/serializers.py)
        so this fallback rule lives in exactly one place."""
        if self.thumbnail_url:
            return self.thumbnail_url
        if self.youtube_video_id:
            return f'https://img.youtube.com/vi/{self.youtube_video_id}/hqdefault.jpg'
        return ''


class VideoProgress(TimeStampedModel):
    """One row per (user, video): a simple started/viewed/completed marker.

    Phase 3 scope only — deliberately NOT trying to sync real YouTube
    playback position/percent (no YouTube IFrame API wiring exists yet).
    `progress_percent` is a coarse, self-reported-by-the-app value (0 until
    completed, 100 once `completed` is set) rather than a real watch-time
    fraction, kept mainly so templates/future work have a number to render
    a progress bar from without a schema change later.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='video_progress',
    )
    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name='progress_entries',
    )

    started_at = models.DateTimeField(default=timezone.now)
    last_viewed_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    progress_percent = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'video'], name='unique_progress_per_user_video'),
        ]
        ordering = ['-last_viewed_at']
        verbose_name = 'Progreso de video'
        verbose_name_plural = 'Progreso de videos'
        indexes = [models.Index(fields=['user', 'completed'])]

    def __str__(self):
        state = 'completado' if self.completed else 'en progreso'
        return f'{self.user} — {self.video} ({state})'

    def mark_completed(self):
        self.completed = True
        self.completed_at = timezone.now()
        self.progress_percent = 100
        self.save(update_fields=['completed', 'completed_at', 'progress_percent', 'updated_at'])


class Favorite(TimeStampedModel):
    """A member bookmarking a video for later — independent of access level,
    so a locked video can be saved as a reminder to watch it once the
    member upgrades. Purely a bookmark: no access decision reads this
    model, only memberships.services.can_access_video does."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='favorites',
    )
    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name='favorited_by',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'video'], name='unique_favorite_per_user_video'),
        ]
        ordering = ['-created_at']
        verbose_name = 'Favorito'
        verbose_name_plural = 'Favoritos'

    def __str__(self):
        return f'{self.user} ♥ {self.video}'
