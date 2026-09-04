from django.contrib import admin

from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'display_name', 'email', 'created_at')
    search_fields = ('user__username', 'user__email', 'display_name')
    readonly_fields = ('user', 'created_at', 'updated_at')
    autocomplete_fields = ()

    fieldsets = (
        (None, {'fields': ('user', 'display_name')}),
        ('Avatar', {'fields': ('avatar', 'avatar_url')}),
        ('Fechas', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def has_add_permission(self, request):
        # Profiles are created automatically via signal when a User is
        # created (see accounts/signals.py) — there's never a reason to
        # add one by hand from the admin.
        return False
