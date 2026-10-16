from django.conf import settings
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Post, Report, Section, Topic


class EnabledAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return settings.FORUM_ENABLED and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return settings.FORUM_ENABLED and super().has_view_permission(request, obj)


@admin.register(Section)
class SectionAdmin(EnabledAdmin):
    list_display = ['name', 'description']

    def has_add_permission(self, request):
        return settings.FORUM_ENABLED and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return settings.FORUM_ENABLED and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(EnabledAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Topic)
class TopicAdmin(ReadOnlyAdmin, SimpleHistoryAdmin):
    list_display = ['title', 'section', 'hidden', 'closed', 'pinned']
    list_filter = ['hidden', 'closed', 'section']


@admin.register(Post)
class PostAdmin(ReadOnlyAdmin, SimpleHistoryAdmin):
    list_display = ['id', 'topic', 'hidden', 'created_at']
    list_filter = ['hidden']


@admin.register(Report)
class ReportAdmin(ReadOnlyAdmin):
    list_display = ['id', 'topic', 'created_at', 'resolved_at']


from .models import ConsentRecord, DeletionRequest


class PrivateLegalAdmin(admin.ModelAdmin):
    actions = None

    def has_module_permission(self, request):
        return request.user.has_perm('water.access_private_registry') and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('water.access_private_registry') and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConsentRecord)
class ConsentRecordAdmin(PrivateLegalAdmin):
    list_display = ('user', 'document_type', 'version', 'action', 'created_at')
    list_filter = ('document_type', 'action')
    readonly_fields = tuple(field.name for field in ConsentRecord._meta.fields)

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DeletionRequest)
class DeletionRequestAdmin(PrivateLegalAdmin, SimpleHistoryAdmin):
    list_display = ('id', 'user', 'created_at', 'status')
    readonly_fields = ('user', 'created_at', 'version')
    exclude = ('history',)

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('water.access_private_registry') and super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        obj._history_user = request.user
        obj._change_reason = 'Рассмотрение заявления об удалении персональных данных'
        super().save_model(request, obj, form, change)
