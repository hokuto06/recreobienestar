from django.contrib import admin

from .models import OfferingPurchase


@admin.register(OfferingPurchase)
class OfferingPurchaseAdmin(admin.ModelAdmin):
    """Phase 4A had no payment provider, so manually creating/editing a row
    here (setting `status` to Completada) was the only way to verify the
    offering's videos unlock. From 4B-1 on, `CheckoutInitiationView`
    creates the PENDING row + `mp_preference_id` automatically — but
    `status` still only becomes COMPLETED by hand here, or (once 4B-2's
    webhook exists) automatically. Editing stays open either way (unlike
    ContactMessageAdmin's read-only stance) — useful for support/refund
    corrections even after the webhook lands."""
    list_display = ('user', 'offering', 'status', 'amount', 'currency', 'created_at')
    list_filter = ('status', 'offering')
    search_fields = (
        'user__username', 'user__email', 'offering__name',
        'mp_preference_id', 'mp_payment_id',
    )
    autocomplete_fields = ('user', 'offering')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)

    fieldsets = (
        (None, {'fields': ('user', 'offering', 'status')}),
        ('Monto (Fase 4B-1)', {
            'fields': ('amount', 'currency'),
            'description': 'Snapshot del precio de la propuesta al momento de iniciar el pago. Vacío en compras cargadas a mano (Fase 4A).',
        }),
        ('Mercado Pago (Fase 4B-1/4B-2)', {
            'fields': ('mp_preference_id', 'mp_payment_id', 'mp_status'),
            'description': 'mp_preference_id lo completa el checkout; mp_payment_id/mp_status quedan vacíos hasta que exista el webhook (Fase 4B-2).',
        }),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
