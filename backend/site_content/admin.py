from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse

from .models import Offering, SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Singleton admin: skips the changelist entirely and goes straight to
    the (only) row's edit form — there's never a meaningful list of
    SiteSettings to browse. Add/delete are disabled; the one row is
    created lazily by SiteSettings.load() the first time anything reads
    it (dashboard, home page API), so there's nothing to seed manually."""
    fieldsets = (
        ('Portada', {'fields': ('hero_headline', 'tagline')}),
        ('Carla', {'fields': ('carla_bio', 'carla_bio_highlight')}),
        ('Contacto y redes', {'fields': ('contact_email', 'instagram_url')}),
        ('Podcast', {'fields': ('podcast_name', 'podcast_url')}),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = SiteSettings.load()
        return redirect(reverse('admin:site_content_sitesettings_change', args=[obj.pk]))


@admin.register(Offering)
class OfferingAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'price', 'currency', 'is_active', 'display_order',
        'has_ars_link', 'has_usd_link',
    )
    list_editable = ('display_order', 'price')
    list_filter = ('is_active', 'currency')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('display_order', 'name')

    fieldsets = (
        (None, {'fields': ('name', 'slug', 'description')}),
        ('Precio', {'fields': ('price', 'currency')}),
        ('Pagos', {'fields': ('payment_url_ars', 'payment_url_usd')}),
        ('Visibilidad', {'fields': ('is_active', 'display_order')}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['activate', 'deactivate']

    @admin.display(description='Link ARS', boolean=True)
    def has_ars_link(self, obj):
        return bool(obj.payment_url_ars)

    @admin.display(description='Link USD', boolean=True)
    def has_usd_link(self, obj):
        return bool(obj.payment_url_usd)

    @admin.action(description='Activar propuestas seleccionadas')
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} propuesta(s) activada(s).', messages.SUCCESS)

    @admin.action(description='Desactivar propuestas seleccionadas')
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} propuesta(s) desactivada(s).', messages.SUCCESS)
