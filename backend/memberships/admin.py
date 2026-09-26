from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import MembershipPlan, Subscription, SubscriptionCharge


@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'subtitle', 'tier', 'visual_variant', 'badge', 'price', 'currency', 'duration_days',
        'trial_days', 'grace_days', 'is_active', 'display_order', 'subscriber_count',
    )
    # `price` editable straight from the changelist — Carla's most common
    # edit — still goes through the model's MinValueValidator on save.
    list_editable = ('display_order', 'price')
    list_filter = ('is_active', 'tier', 'visual_variant', 'currency')
    search_fields = ('name', 'subtitle', 'description')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('display_order', 'name')

    fieldsets = (
        (None, {'fields': ('tier', 'name', 'subtitle', 'slug')}),
        ('Beneficios', {
            'fields': ('description',),
            'description': 'Un beneficio por línea. Cada línea aparece como un ítem con tilde en la tarjeta.',
        }),
        ('Presentación', {'fields': ('badge', 'visual_variant', 'cta_label')}),
        ('Precio', {'fields': ('price', 'currency', 'duration_days')}),
        ('Suscripción recurrente', {'fields': ('trial_days', 'grace_days')}),
        ('Visibilidad', {'fields': ('is_active', 'display_order')}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['activate', 'deactivate']

    @admin.display(description='Suscripciones activas')
    def subscriber_count(self, obj):
        return sum(1 for s in obj.subscriptions.all() if s.is_active())

    @admin.action(description='Activar planes seleccionados')
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} plan(es) activado(s).', messages.SUCCESS)

    @admin.action(description='Desactivar planes seleccionados')
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} plan(es) desactivado(s).', messages.SUCCESS)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'plan', 'status', 'current_access_badge',
        'starts_at', 'ends_at', 'trial_ends_at', 'grace_ends_at', 'cancelled_at',
    )
    list_filter = ('status', 'plan')
    search_fields = ('user__username', 'user__email', 'plan__name')
    autocomplete_fields = ('user', 'plan')
    readonly_fields = (
        'created_at', 'updated_at',
        'mp_preapproval_id', 'mp_init_point', 'mp_payer_id', 'mp_status', 'next_payment_date',
        'last_charge_payment_id', 'last_charge_status', 'amount', 'currency', 'superseded_by',
    )
    date_hierarchy = 'starts_at'

    fieldsets = (
        (None, {'fields': ('user', 'plan', 'status')}),
        ('Vigencia', {'fields': ('starts_at', 'ends_at', 'cancelled_at')}),
        # trial_ends_at/grace_ends_at: hoy solo grace_ends_at afecta el
        # acceso (extiende la ventana de is_active() más allá de ends_at —
        # ver Subscription._effective_ends_at); trial_ends_at todavía no
        # tiene efecto (Fase 5A), pero ya es editable a mano acá para
        # probar el flujo sin depender de Mercado Pago, igual que 4A.
        ('Suscripción recurrente (Fase 5A)', {'fields': ('trial_ends_at', 'grace_ends_at')}),
        # Fase 5B-2a: solo lectura — los completa el alta vía Mercado Pago
        # (y, desde 5B-2b, el webhook de suscripciones), nunca a mano.
        ('Mercado Pago (Fase 5B-2a)', {'fields': (
            'mp_preapproval_id', 'mp_init_point', 'mp_status', 'mp_payer_id', 'amount', 'currency',
            'next_payment_date', 'last_charge_payment_id', 'last_charge_status', 'superseded_by',
        ), 'classes': ('collapse',)}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['mark_cancelled', 'mark_active']

    @admin.display(description='Acceso actual')
    def current_access_badge(self, obj):
        active = obj.is_active()
        color = '#2e7d32' if active else '#b71c1c'
        label = 'Con acceso' if active else 'Sin acceso'
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:10px;font-size:11px;white-space:nowrap">{}</span>',
            color, label,
        )

    @admin.action(description='Cancelar suscripciones seleccionadas')
    def mark_cancelled(self, request, queryset):
        updated = queryset.update(status='cancelled', cancelled_at=timezone.now())
        self.message_user(request, f'{updated} suscripción(es) cancelada(s).', messages.SUCCESS)

    @admin.action(description='Marcar como activa (sin tocar fechas)')
    def mark_active(self, request, queryset):
        updated = queryset.update(status='active')
        self.message_user(
            request,
            f'{updated} suscripción(es) marcada(s) como activa(s). '
            'Revisá ends_at si corresponde extender la vigencia.',
            messages.SUCCESS,
        )


@admin.register(SubscriptionCharge)
class SubscriptionChargeAdmin(admin.ModelAdmin):
    """Fase 5B-2b: registro de cobros recurrentes de Mercado Pago — solo
    lectura. Lo escribe únicamente el webhook de suscripciones."""
    list_display = (
        'subscription', 'mp_payment_id', 'mp_payment_status', 'amount', 'currency',
        'outcome', 'created_at',
    )
    list_filter = ('outcome', 'mp_payment_status')
    search_fields = ('mp_payment_id', 'mp_authorized_payment_id', 'subscription__user__email')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
