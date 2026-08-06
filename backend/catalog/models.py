from django.db import models

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
