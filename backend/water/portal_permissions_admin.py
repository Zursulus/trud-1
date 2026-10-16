from django.contrib import admin

from .admin import RecordedAdmin
from .portal_permissions import PortalGrant
from .privacy_admin import PrivateRegistryPermissionMixin


@admin.register(PortalGrant)
class PortalGrantAdmin(PrivateRegistryPermissionMixin, RecordedAdmin):
    list_display = (
        'person', 'account', 'starts', 'ends',
        'can_view_finance', 'can_submit_water', 'can_view_documents',
        'can_use_appeals', 'can_represent', 'verified_by',
    )
    list_filter = (
        'can_view_finance', 'can_submit_water', 'can_view_documents',
        'can_use_appeals', 'can_represent',
    )
    search_fields = ('person__full_name', 'account__number', 'account__plot', 'basis')
    autocomplete_fields = ('person', 'account', 'verified_by')
    readonly_fields = ('verified_at',)
    list_select_related = ('person', 'account', 'verified_by')

    def has_view_history_permission(self, request, obj=None):
        return self.has_view_permission(request, obj) and super().has_view_history_permission(request, obj)

    def has_change_history_permission(self, request, obj=None):
        return self.has_view_permission(request, obj) and super().has_change_history_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.verified_by_id:
            obj.verified_by = request.user
        super().save_model(request, obj, form, change)
