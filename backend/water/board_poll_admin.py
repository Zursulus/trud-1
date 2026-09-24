from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .board_polls import (
    BoardAuditEvent,
    BoardDiscussionComment,
    BoardMembership,
    BoardPoll,
    BoardProtocol,
    BoardQuestion,
    BoardVote,
)


class BoardQuestionInline(admin.TabularInline):
    model = BoardQuestion
    extra = 1
    fields = ('order', 'text')
    show_change_link = True

    def has_delete_permission(self, request, obj=None):
        return False


class BoardProtocolInline(admin.StackedInline):
    model = BoardProtocol
    extra = 0
    max_num = 1
    fields = ('document', 'original_name', 'file_size', 'uploaded_by', 'uploaded_at')
    readonly_fields = ('original_name', 'file_size', 'uploaded_by', 'uploaded_at')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BoardMembership)
class BoardMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'starts', 'ends')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    fields = ('user', 'role', 'starts', 'ends', 'basis', 'notes', 'version', 'created_at', 'updated_at')
    readonly_fields = ('version', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        obj._audit_actor = request.user
        obj._audit_reason = 'Изменение состава правления' if change else 'Добавление в состав правления'
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BoardPoll)
class BoardPollAdmin(admin.ModelAdmin):
    list_display = ('title', 'opens_at', 'closes_at', 'closed_at', 'created_by')
    list_filter = ('closed_at',)
    search_fields = ('title', 'description')
    inlines = (BoardQuestionInline, BoardProtocolInline)
    fields = ('title', 'description', 'opens_at', 'closes_at', 'closed_at', 'created_by', 'version', 'created_at', 'updated_at')
    readonly_fields = ('created_by', 'version', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        obj._audit_actor = request.user
        obj._audit_reason = 'Изменение предварительного опроса' if change else 'Создание предварительного опроса'
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, BoardQuestion):
                instance._audit_actor = request.user
                instance._audit_reason = 'Вопрос предварительного опроса'
            if isinstance(instance, BoardProtocol) and not instance.pk:
                instance.uploaded_by = request.user
            instance.save()
        formset.save_m2m()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BoardQuestion)
class BoardQuestionAdmin(admin.ModelAdmin):
    list_display = ('poll', 'order', 'text', 'version')
    list_filter = ('poll',)
    search_fields = ('text', 'poll__title')
    fields = ('poll', 'order', 'text', 'version', 'created_at', 'updated_at')
    readonly_fields = ('version', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        obj._audit_actor = request.user
        obj._audit_reason = 'Изменение вопроса' if change else 'Добавление вопроса'
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BoardVote)
class BoardVoteAdmin(admin.ModelAdmin):
    list_display = ('question', 'user', 'choice', 'cast_at', 'updated_at')
    list_filter = ('choice', 'question__poll')
    search_fields = ('user__username', 'question__text')
    readonly_fields = ('question', 'user', 'choice', 'comment', 'cast_at', 'created_at', 'updated_at', 'version')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(request.method in ('GET', 'HEAD', 'OPTIONS'))

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BoardDiscussionComment)
class BoardDiscussionCommentAdmin(admin.ModelAdmin):
    list_display = ('question', 'author', 'created_at')
    readonly_fields = ('question', 'author', 'body', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(request.method in ('GET', 'HEAD', 'OPTIONS'))

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BoardProtocol)
class BoardProtocolAdmin(admin.ModelAdmin):
    list_display = ('poll', 'original_name', 'file_size', 'uploaded_by', 'uploaded_at')
    readonly_fields = ('original_name', 'file_size', 'uploaded_by', 'uploaded_at')

    def save_model(self, request, obj, form, change):
        if change:
            raise PermissionDenied('Сохранённый протокол нельзя изменять.')
        obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BoardAuditEvent)
class BoardAuditEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'target_type', 'target_id', 'action', 'actor', 'summary')
    list_filter = ('target_type', 'action')
    readonly_fields = ('target_type', 'target_id', 'action', 'actor', 'summary', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(request.method in ('GET', 'HEAD', 'OPTIONS'))

    def has_delete_permission(self, request, obj=None):
        return False
