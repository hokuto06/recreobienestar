from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from common.choices import ENTITLED_STATUSES, PlanTier, SubscriptionStatus
from common.models import OrderedActiveModel, TimeStampedModel
from common.text import generate_unique_slug


class MembershipPlanVisualVariant(models.TextChoices):
    """Which card treatment the home page uses (Phase 3.7 visual
    reference): a plain default card, the highlighted/"most recommended"
    mint card, or the dark premium card. Purely presentational — has no
    effect on access control, which is governed entirely by `tier`."""
    DEFAULT = 'default', 'Estándar (tarjeta blanca)'
    HIGHLIGHTED = 'highlighted', 'Destacado (tarjeta menta, "más recomendado")'
    PREMIUM = 'premium', 'Premium (tarjeta oscura)'


class MembershipPlan(OrderedActiveModel, TimeStampedModel):
    tier = models.CharField(
        max_length=20, choices=PlanTier.choices, unique=True,
        help_text='Identificador fijo del plan (no editable desde el admin).',
    )
    name = models.CharField(
        max_length=150,
        help_text='Nombre corto/etiqueta mostrada en mayúsculas arriba del título (ej. "Plan Lumbar").',
    )
    subtitle = models.CharField(
        max_length=150, blank=True,
        help_text='Título grande de la tarjeta (ej. "Alivio Lumbar"). Si se deja vacío, se usa el nombre.',
    )
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    description = models.TextField(
        blank=True,
        help_text='Un beneficio por línea — cada línea se muestra como un ítem con tilde en la tarjeta.',
    )
    badge = models.CharField(
        max_length=40, blank=True,
        help_text='Etiqueta superpuesta arriba de la tarjeta (ej. "Más recomendado"). Vacío = sin etiqueta.',
    )
    visual_variant = models.CharField(
        max_length=20, choices=MembershipPlanVisualVariant.choices,
        default=MembershipPlanVisualVariant.DEFAULT,
    )
    cta_label = models.CharField(
        max_length=60, blank=True,
        help_text='Texto del botón (ej. "Empezar alivio"). Vacío = "Sumarme".',
    )

    price = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'), message='El precio no puede ser negativo.')],
    )
    currency = models.CharField(max_length=3, default='ARS')

    duration_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Duración en días. Vacío = sin vencimiento automático por duración.',
    )

    class Meta(OrderedActiveModel.Meta):
        verbose_name = 'Plan de membresía'
        verbose_name_plural = 'Planes de membresía'

    def __str__(self):
        return f'{self.name} ({self.get_tier_display()})'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, self.name)
        super().save(*args, **kwargs)


class Subscription(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscriptions',
    )
    plan = models.ForeignKey(
        MembershipPlan, on_delete=models.PROTECT, related_name='subscriptions',
    )
    status = models.CharField(
        max_length=20, choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIAL,
    )

    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Vacío = sin fecha de fin definida (no recomendado en producción).',
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Suscripción'
        verbose_name_plural = 'Suscripciones'

    def __str__(self):
        return f'{self.user} — {self.plan} ({self.get_status_display()})'

    def is_expired(self, at=None):
        """True once ends_at has passed, regardless of what `status` says —
        the stored status can lag reality (e.g. a cron hasn't run yet), but
        access must be denied the instant the membership's time is up."""
        if self.ends_at is None:
            return False
        moment = at or timezone.now()
        return self.ends_at <= moment

    def is_active(self, at=None):
        """Currently entitled: status reflects an entitled state AND it
        hasn't expired yet. Expiry always wins over a stale 'active' status."""
        if self.status not in ENTITLED_STATUSES:
            return False
        return not self.is_expired(at=at)
