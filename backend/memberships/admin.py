from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import MembershipPlan, Subscription


@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'subtitle', 'tier', 'visual_variant', 'badge', 'price', 'currency', 'duration_days',
        'is_active', 'display_order', 'subscriber_count',
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
        'starts_at', 'ends_at', 'cancelled_at',
    )
    list_filter = ('status', 'plan')
    search_fields = ('user__username', 'user__email', 'plan__name')
    autocomplete_fields = ('user', 'plan')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'starts_at'

    fieldsets = (
        (None, {'fields': ('user', 'plan', 'status')}),
        ('Vigencia', {'fields': ('starts_at', 'ends_at', 'cancelled_at')}),
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
