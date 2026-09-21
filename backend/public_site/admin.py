from django import forms
from django.contrib import admin
from django.utils import timezone

from .models import PublicDocument, PublicDocumentCategory, PublicNews


class AuditedAdminForm(forms.ModelForm):
    change_reason = forms.CharField(
        label='Причина изменения', required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def clean(self):
        data = super().clean()
        if self.instance.pk and not data.get('change_reason', '').strip():
            self.add_error('change_reason', 'При изменении существующей записи укажите причину.')
        return data


class PublicationAdminForm(AuditedAdminForm):
    confirm_publication = forms.BooleanField(
        label='Проверено: в публикации нет персональных, банковских и служебных данных',
        required=False,
        help_text='Для каждой сохранённой опубликованной версии подтверждение ставится заново.',
    )

    def clean(self):
        data = super().clean()
        if data.get('is_published') and not data.get('confirm_publication'):
            self.add_error(
                'confirm_publication',
                'Перед публикацией проверьте материал и подтвердите отсутствие закрытых данных.',
            )
        return data


class NoDeleteAuditedAdmin(admin.ModelAdmin):
    actions = None
    list_per_page = 30
    form = AuditedAdminForm

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj._change_reason = form.cleaned_data.get('change_reason') or 'Создание записи'
        super().save_model(request, obj, form, change)

    def log_change(self, request, obj, message):
        reason = getattr(obj, '_change_reason', '')
        if isinstance(message, list):
            message = [*message, {'changed': {'fields': [f'Причина: {reason}']}}]
        else:
            message = f'{message}; Причина: {reason}'
        return super().log_change(request, obj, message)


class PublicationAdmin(NoDeleteAuditedAdmin):
    form = PublicationAdminForm

    def get_readonly_fields(self, request, obj=None):
        return ('public_checked', 'published_at', 'published_by', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        if obj.is_published:
            obj.public_checked = True
            obj.published_at = timezone.now()
            obj.published_by = request.user
        else:
            obj.public_checked = False
            obj.published_at = None
            obj.published_by = None
        super().save_model(request, obj, form, change)


@admin.register(PublicNews)
class PublicNewsAdmin(PublicationAdmin):
    list_display = ('title', 'category', 'published_on', 'is_featured', 'is_published', 'published_by')
    list_filter = ('is_published', 'is_featured', 'category', 'published_on')
    search_fields = ('title', 'summary', 'body', 'category')
    date_hierarchy = 'published_on'


@admin.register(PublicDocumentCategory)
class PublicDocumentCategoryAdmin(NoDeleteAuditedAdmin):
    list_display = ('name', 'active', 'sort_order')
    list_filter = ('active',)
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(PublicDocument)
class PublicDocumentAdmin(PublicationAdmin):
    list_display = ('title', 'category', 'document_date', 'is_published', 'published_by', 'file_size')
    list_filter = ('is_published', 'category', 'document_date')
    search_fields = ('title', 'description', 'original_name', 'notes')
    date_hierarchy = 'document_date'

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj)) + ('original_name', 'file_size')
        return fields + (('document',) if obj else ())
