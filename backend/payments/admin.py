from django.contrib import admin

from .models import OfferingPurchase


@admin.register(OfferingPurchase)
class OfferingPurchaseAdmin(admin.ModelAdmin):
    """Phase 4A has no payment provider yet, so this is the only way a
    purchase gets recorded/confirmed: Carla (or whoever's testing this
    phase) creates/edits a row here by hand, setting `status` to
    Completada to verify the offering's videos unlock. Editing is
    intentionally left open (unlike ContactMessageAdmin's read-only
    stance) — see the model docstring; Phase 4B's webhook will eventually
    do this automatically, but manual editing stays available even then
    for support/refund corrections."""
    list_display = ('user', 'offering', 'status', 'created_at')
    list_filter = ('status', 'offering')
    search_fields = ('user__username', 'user__email', 'offering__name')
    autocomplete_fields = ('user', 'offering')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)

    fieldsets = (
        (None, {'fields': ('user', 'offering', 'status')}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
