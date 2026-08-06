from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Category, Program, Video


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'display_order', 'video_count', 'updated_at')
    list_editable = ('display_order',)
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('display_order', 'name')

    fieldsets = (
        (None, {'fields': ('name', 'slug', 'description')}),
        ('Visibilidad', {'fields': ('is_active', 'display_order')}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['activate', 'deactivate']

    @admin.display(description='Videos')
    def video_count(self, obj):
        return obj.videos.count()

    @admin.action(description='Activar categorías seleccionadas')
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} categoría(s) activada(s).', messages.SUCCESS)

    @admin.action(description='Desactivar categorías seleccionadas')
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} categoría(s) desactivada(s).', messages.SUCCESS)


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'display_order', 'video_count', 'updated_at')
    list_editable = ('display_order',)
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at', 'cover_preview')
    ordering = ('display_order', 'name')

    fieldsets = (
        (None, {'fields': ('name', 'slug', 'description')}),
        ('Portada', {'fields': ('cover_image', 'cover_image_url', 'cover_preview')}),
        ('Visibilidad', {'fields': ('is_active', 'display_order')}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['activate', 'deactivate']

    @admin.display(description='Videos')
    def video_count(self, obj):
        return obj.videos.count()

    @admin.display(description='Vista previa')
    def cover_preview(self, obj):
        url = obj.cover_image_display_url
        if not url:
            return '—'
        return format_html('<img src="{}" style="max-height:120px;border-radius:4px" />', url)

    @admin.action(description='Activar programas seleccionados')
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} programa(s) activado(s).', messages.SUCCESS)

    @admin.action(description='Desactivar programas seleccionados')
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} programa(s) desactivado(s).', messages.SUCCESS)


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'category', 'program', 'access_level_badge',
        'is_published', 'is_featured', 'display_order', 'duration_label',
        'publication_date',
    )
    list_editable = ('display_order',)
    list_filter = ('is_published', 'is_featured', 'access_level', 'category', 'program')
    search_fields = ('title', 'short_description', 'full_description', 'youtube_video_id')
    list_select_related = ('category', 'program')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('youtube_video_id', 'thumbnail_preview', 'created_at', 'updated_at')
    autocomplete_fields = ('category', 'program')
    date_hierarchy = 'publication_date'
    ordering = ('display_order', '-publication_date')

    fieldsets = (
        (None, {'fields': ('title', 'slug', 'short_description', 'full_description')}),
        ('Video de YouTube', {
            'fields': ('youtube_url', 'youtube_video_id', 'thumbnail_url', 'thumbnail_preview'),
        }),
        ('Clasificación', {'fields': ('category', 'program', 'access_level')}),
        ('Publicación', {
            'fields': ('is_published', 'is_featured', 'display_order', 'duration_label', 'publication_date'),
        }),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['publish', 'unpublish', 'mark_free']

    @admin.display(description='Acceso')
    def access_level_badge(self, obj):
        colors = {
            'free': '#2e7d32',
            'plan1': '#1565c0',
            'plan2': '#6a1b9a',
            'all_paid': '#b71c1c',
        }
        color = colors.get(obj.access_level, '#555')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:10px;font-size:11px;white-space:nowrap">{}</span>',
            color, obj.get_access_level_display(),
        )

    @admin.display(description='Miniatura')
    def thumbnail_preview(self, obj):
        url = obj.thumbnail_display_url
        if not url:
            return '—'
        return format_html('<img src="{}" style="max-height:120px;border-radius:4px" />', url)

    @admin.action(description='Publicar videos seleccionados')
    def publish(self, request, queryset):
        updated = queryset.update(is_published=True)
        self.message_user(request, f'{updated} video(s) publicado(s).', messages.SUCCESS)

    @admin.action(description='Ocultar (despublicar) videos seleccionados')
    def unpublish(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(request, f'{updated} video(s) ocultado(s).', messages.SUCCESS)

    @admin.action(description='Marcar como gratuitos')
    def mark_free(self, request, queryset):
        updated = queryset.update(access_level='free')
        self.message_user(request, f'{updated} video(s) marcado(s) como gratuitos.', messages.SUCCESS)
