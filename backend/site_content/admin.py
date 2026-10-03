from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse

from .models import ContactMessage, Offering, SiteSettings, Testimonial


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Singleton admin: skips the changelist entirely and goes straight to
    the (only) row's edit form — there's never a meaningful list of
    SiteSettings to browse. Add/delete are disabled; the one row is
    created lazily by SiteSettings.load() the first time anything reads
    it (dashboard, home page API), so there's nothing to seed manually."""
    fieldsets = (
        ('Portada', {'fields': ('hero_headline', 'tagline')}),
        ('Carla', {'fields': ('carla_photo', 'carla_bio', 'carla_bio_highlight')}),
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


class OfferingAdminForm(forms.ModelForm):
    """The deliverable lives in private storage with no public URL, so
    Django's default ClearableFileInput (which links to the file's URL)
    is replaced by a plain file input + an explicit "remove" checkbox; the
    current file is described by OfferingAdmin.deliverable_status."""
    remove_deliverable = forms.BooleanField(
        required=False, label='Quitar el PDF actual',
        help_text='Marcalo para dejar esta propuesta sin PDF (si además subís uno nuevo, se usa el nuevo).',
    )

    class Meta:
        model = Offering
        fields = '__all__'
        widgets = {'deliverable': forms.FileInput(attrs={'accept': 'application/pdf,.pdf'})}


@admin.register(Offering)
class OfferingAdmin(admin.ModelAdmin):
    form = OfferingAdminForm
    list_display = (
        'name', 'price', 'currency', 'is_active', 'display_order',
        'has_ars_link', 'has_usd_link',
    )
    list_editable = ('display_order', 'price')
    list_filter = ('is_active', 'currency')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at', 'deliverable_status')
    ordering = ('display_order', 'name')
    # filter_horizontal rather than the default multi-select box: picking
    # videos into a package is easier as a searchable two-pane widget once
    # the catalog has more than a handful of videos.
    filter_horizontal = ('videos',)

    fieldsets = (
        (None, {'fields': ('name', 'slug', 'description')}),
        ('Precio', {'fields': ('price', 'currency')}),
        ('Pagos', {'fields': ('payment_url_ars', 'payment_url_usd')}),
        ('Acceso (Fase 4A)', {
            'fields': ('videos',),
            'description': 'Videos que se desbloquean al comprar esta propuesta. Opcional.',
        }),
        ('PDF para descargar', {
            'fields': ('deliverable_status', 'deliverable', 'remove_deliverable'),
            'description': (
                'Opcional. Lo descargan solo quienes compraron la propuesta, desde su cuenta y desde '
                'el mail de confirmación. El archivo no queda publicado en ninguna dirección pública.'
            ),
        }),
        ('Visibilidad', {'fields': ('is_active', 'display_order')}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['activate', 'deactivate']

    @admin.display(description='PDF actual')
    def deliverable_status(self, obj):
        if not obj or not obj.deliverable:
            return 'Sin PDF.'
        try:
            size = f'{obj.deliverable.size / (1024 * 1024):.1f} MB'
        except OSError:
            return 'Hay un PDF registrado, pero el archivo no se encuentra. Volvé a subirlo.'
        return f'PDF cargado ({size}). Quienes la compraron lo descargan en /propuestas/{obj.slug}/descargar/.'

    def save_model(self, request, obj, form, change):
        previous = None
        if change:
            previous = Offering.objects.filter(pk=obj.pk).values_list('deliverable', flat=True).first()
        new_upload = 'deliverable' in form.changed_data and form.cleaned_data.get('deliverable')
        if form.cleaned_data.get('remove_deliverable') and not new_upload:
            obj.deliverable = ''
        super().save_model(request, obj, form, change)
        # Replaced or removed: delete the old file so no orphaned paid PDF
        # lingers in storage.
        if previous and previous != obj.deliverable.name:
            obj.deliverable.storage.delete(previous)

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


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ('author_name', 'rating', 'display_order', 'is_active')
    list_editable = ('display_order', 'is_active')
    list_filter = ('is_active', 'rating')
    search_fields = ('author_name', 'text')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('display_order',)

    fieldsets = (
        (None, {'fields': ('author_name', 'text', 'rating')}),
        ('Visibilidad', {'fields': ('is_active', 'display_order')}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['activate', 'deactivate']

    @admin.action(description='Activar reseñas seleccionadas')
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} reseña(s) activada(s).', messages.SUCCESS)

    @admin.action(description='Desactivar reseñas seleccionadas')
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} reseña(s) desactivada(s).', messages.SUCCESS)


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """Read-only submissions from POST /api/contacto/ — Carla only ever
    toggles is_read here, never edits or creates a message by hand (see
    has_add_permission below), same spirit as SiteSettingsAdmin disabling
    actions that don't make sense for its data."""
    list_display = ('name', 'email', 'created_at', 'is_read')
    list_editable = ('is_read',)
    list_filter = ('is_read', 'created_at')
    search_fields = ('name', 'email', 'message')
    readonly_fields = ('name', 'email', 'message', 'created_at', 'updated_at')
    ordering = ('-created_at',)

    fieldsets = (
        (None, {'fields': ('name', 'email', 'message')}),
        ('Estado', {'fields': ('is_read',)}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def has_add_permission(self, request):
        return False
