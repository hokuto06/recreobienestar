from datetime import timedelta
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

    # ── Phase 5A: suscripción recurrente — configuración por plan ──────────
    # Solo el plazo (en días); qué se hace con estos plazos vive en
    # Subscription (trial_ends_at/grace_ends_at) y su is_active(). No
    # afectan a las suscripciones creadas a mano hoy (5A no cambia cómo se
    # crea una Subscription, solo qué campos puede tener).
    trial_days = models.PositiveIntegerField(
        default=7,
        help_text='Días de prueba gratuita antes del primer cobro. 0 = sin período de prueba.',
    )
    grace_days = models.PositiveIntegerField(
        default=5,
        help_text=(
            'Días de gracia con acceso mantenido después de un cobro fallido, '
            'mientras Mercado Pago reintenta el cobro. 0 = sin período de gracia '
            '(se pierde el acceso apenas falla el cobro).'
        ),
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

    # ── Phase 5B-1: prueba gratuita de 7 días ──────────────────────────────
    # True SOLO para la fila creada por memberships.views.StartTrialView —
    # nunca cambia después (ni al expirar, cancelarse, etc.), a propósito:
    # es lo que permite hacer cumplir "una prueba por usuario, para
    # siempre" con un UniqueConstraint (abajo) en vez de depender de
    # `status`, que sí puede cambiar con el tiempo.
    is_trial = models.BooleanField(
        default=False,
        help_text='Marca la suscripción creada por la prueba gratuita de 7 días. No se edita a mano.',
    )

    # ── Phase 5A: suscripción recurrente — campos de ciclo de vida ─────────
    # Solo lo que 5A necesita para el acceso durante prueba/gracia — los
    # campos específicos de Mercado Pago (preapproval id, payer id, último
    # cobro, etc.) llegan en 5B, no acá.
    trial_ends_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Fin del período de prueba gratuita. No afecta el acceso todavía (Fase 5A).',
    )
    grace_ends_at = models.DateTimeField(
        null=True, blank=True,
        help_text=(
            'Fin del período de gracia tras un cobro fallido — mientras esta fecha no haya '
            'pasado, el acceso se mantiene aunque ends_at ya haya pasado. Se completa al '
            'fallar un cobro (start_grace) y se limpia al cobrar con éxito (clear_grace).'
        ),
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Suscripción'
        verbose_name_plural = 'Suscripciones'
        constraints = [
            # DB-level enforcement of "one free trial per user, ever" —
            # the application-level check in StartTrialView is the normal
            # path (a clear error before ever attempting the insert), this
            # constraint is the race-safety net for two concurrent
            # double-submits (the second INSERT raises IntegrityError,
            # which the view catches and turns into the same rejection).
            # Scoped to is_trial=True only, so it never constrains a
            # user's ordinary paid-plan subscriptions (e.g. resubscribing
            # to the same plan later).
            models.UniqueConstraint(
                fields=['user'], condition=models.Q(is_trial=True),
                name='one_trial_subscription_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.user} — {self.plan} ({self.get_status_display()})'

    def _effective_ends_at(self):
        """The moment access actually stops, factoring in a grace period
        that's currently stamped (Phase 5A) — see the module-level note on
        ENTITLED_STATUSES/PAST_DUE for the product rule this implements: a
        failed charge extends the access window via grace_ends_at rather
        than flipping the status.

        A grace stamp only ever EXTENDS the window, never shortens it: if
        grace_ends_at is set but not later than ends_at (e.g. a stale grace
        stamp nobody cleared, or one set by mistake earlier than the
        subscription's own end), it's ignored and ends_at alone governs —
        the exact behavior as before Phase 5A.

        No ends_at at all means "never expires" (unchanged from before) —
        there's nothing for a grace period to extend past, so grace_ends_at
        is ignored in that case too."""
        if self.ends_at is None:
            return None
        if self.grace_ends_at is not None and self.grace_ends_at > self.ends_at:
            return self.grace_ends_at
        return self.ends_at

    def is_expired(self, at=None):
        """True once the effective end of access (ends_at, possibly
        extended by an in-effect grace period — see _effective_ends_at)
        has passed, regardless of what `status` says — the stored status
        can lag reality (e.g. a cron hasn't run yet), but access must be
        denied the instant the membership's time is up."""
        effective_ends_at = self._effective_ends_at()
        if effective_ends_at is None:
            return False
        moment = at or timezone.now()
        return effective_ends_at <= moment

    def is_active(self, at=None):
        """Currently entitled: status reflects an entitled state AND it
        hasn't expired yet (factoring in a grace period, if one is in
        effect — see is_expired). Expiry always wins over a stale 'active'
        status.

        A grace period only ever matters here for a status already in
        ENTITLED_STATUSES: PAST_DUE and EXPIRED deny access unconditionally
        (the `status not in ENTITLED_STATUSES` check below returns before
        grace_ends_at is ever consulted), by design — grace extends an
        otherwise-live subscription's window, it does not resurrect one
        that's already been marked dead."""
        if self.status not in ENTITLED_STATUSES:
            return False
        return not self.is_expired(at=at)

    def start_grace(self, plan=None):
        """Stamps grace_ends_at = now + (plan or self.plan).grace_days,
        extending access past a failed charge without touching `status` —
        see is_active's docstring for why grace is a window extension, not
        a status change. Does NOT call save() — the caller decides when/
        with which update_fields, exactly like OfferingPurchase's status
        transitions in payments.services.

        Not called from anywhere yet: this is the entry point Phase 5B's
        webhook-driven charge-failure handling will use once it exists.

        `plan`: optional override (defaults to self.plan) so a caller that
        already has the plan loaded avoids an extra query.
        """
        plan = plan or self.plan
        self.grace_ends_at = timezone.now() + timedelta(days=plan.grace_days)

    def clear_grace(self):
        """Clears an in-effect grace period — the counterpart to
        start_grace(), for once a retried charge succeeds. Does NOT call
        save() (see start_grace). Not called from anywhere yet."""
        self.grace_ends_at = None
