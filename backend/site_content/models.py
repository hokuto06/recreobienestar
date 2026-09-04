"""
Marketing/site-wide content, editable from Django Admin, deliberately kept
separate from catalog (Video/Category/Program) and memberships
(MembershipPlan/Subscription):

- SiteSettings holds copy that appears once, site-wide (hero headline,
  tagline, Carla's bio, contact/social links) — a singleton, not a list.
- Offering holds Carla's one-time-purchase products (with Mercado Pago
  links), which are NOT membership subscription tiers and do NOT gate
  video access — see memberships.services.can_access_video, which this
  model is never consulted by. Kept distinct from MembershipPlan
  specifically so "what one-time products exist" and "what recurring plan
  tier gates this video" never get conflated in the same table.
"""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from common.models import OrderedActiveModel, TimeStampedModel
from common.text import generate_unique_slug


class SiteSettings(TimeStampedModel):
    """Singleton — always exactly one row (pk=1). Use SiteSettings.load()
    to fetch it (creates the row with blank defaults on first access, so
    there's nothing to migrate/seed manually)."""
    tagline = models.CharField(
        max_length=200, blank=True,
        help_text='Frase breve de marca. Ej: eslogan mostrado en el hero y el pie de página.',
    )
    hero_headline = models.CharField(
        max_length=200, blank=True,
        help_text='Título principal de la portada.',
    )
    carla_bio = models.TextField(
        blank=True,
        help_text='Biografía de Carla. Separá párrafos con una línea en blanco.',
    )
    carla_bio_highlight = models.TextField(
        blank=True,
        help_text=(
            'Frase destacada de Carla, mostrada aparte de la biografía '
            'principal con un tratamiento visual propio (cita destacada). '
            'Ej: "ReCREO, me permite expresar mi esencia…".'
        ),
    )
    contact_email = models.EmailField(blank=True)
    instagram_url = models.URLField(blank=True)
    podcast_name = models.CharField(max_length=150, blank=True)
    podcast_url = models.URLField(
        blank=True,
        help_text='Si se deja vacío, la sección de podcast no se muestra en la portada.',
    )

    class Meta:
        verbose_name = 'Configuración del sitio'
        verbose_name_plural = 'Configuración del sitio'

    def __str__(self):
        return 'Configuración del sitio'

    def save(self, *args, **kwargs):
        # Enforce the singleton: always row 1, regardless of what created
        # the instance (admin "add" form, load(), a freshly-constructed
        # SiteSettings(...), etc.).
        self.pk = 1
        # A freshly-constructed instance never had created_at set (it's
        # auto_now_add, only auto-populated on INSERT) — but pk=1 usually
        # already exists once the site has been used at all (see the
        # site_content 0003 seed migration), so this save() becomes an
        # UPDATE, not an INSERT, and would otherwise overwrite the
        # existing row's created_at with NULL. Preserve it instead.
        if self.created_at is None:
            existing = type(self).objects.filter(pk=1).values_list('created_at', flat=True).first()
            if existing is not None:
                self.created_at = existing
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Never actually deletable — there's always supposed to be exactly
        # one row. (Admin also disables the delete action — see admin.py.)
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Offering(OrderedActiveModel, TimeStampedModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    description = models.TextField(blank=True)

    price = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'), message='El precio no puede ser negativo.')],
    )
    currency = models.CharField(max_length=3, default='ARS')

    # Both optional and independent: a payment method with no link yet
    # simply isn't offered for that currency (see catalog templates /
    # home-dynamic.js, which hide the corresponding button rather than
    # link to a blank/placeholder URL).
    payment_url_ars = models.URLField(
        blank=True, help_text='Link de Mercado Pago (u otro medio) para pagar en ARS.',
    )
    payment_url_usd = models.URLField(
        blank=True, help_text='Link de pago en USD. Opcional.',
    )

    class Meta(OrderedActiveModel.Meta):
        verbose_name = 'Propuesta'
        verbose_name_plural = 'Propuestas'

    def __str__(self):
        return f'{self.name} ({self.price} {self.currency})'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, self.name)
        super().save(*args, **kwargs)


class Testimonial(OrderedActiveModel, TimeStampedModel):
    """Reseñas de alumnas/alumnos, cargadas por Carla desde el Admin —
    mismo patrón editable/publicado que Offering (display_order + is_active
    heredados de OrderedActiveModel). is_active deliberadamente NO se
    expone en la API pública (ver serializers.py): sirve solo para que
    Carla oculte una reseña sin borrarla, no como dato de marketing."""
    author_name = models.CharField(max_length=150)
    text = models.TextField(help_text='Texto de la reseña.')
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='Puntaje de 1 a 5 estrellas.',
    )

    class Meta(OrderedActiveModel.Meta):
        verbose_name = 'Reseña'
        verbose_name_plural = 'Reseñas'

    def __str__(self):
        return f'{self.author_name} ({self.rating}★)'
