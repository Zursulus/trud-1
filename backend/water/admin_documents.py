"""Download links for files whose storage paths are deliberately not served."""
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html


class ProtectedDocumentAdminMixin:
    document_download_url = None

    def get_exclude(self, request, obj=None):
        fields = tuple(super().get_exclude(request, obj) or ())
        return fields + ('document',) if obj else fields or None

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields + ('document_download',) if obj else fields

    def document_download_id(self, obj):
        return obj.pk

    @admin.display(description='Файл')
    def document_download(self, obj):
        if not obj.document:
            return '—'
        return format_html(
            '<a href="{}">{}</a>',
            reverse(self.document_download_url, args=[self.document_download_id(obj)]),
            obj.original_name or obj.document.name.rsplit('/', 1)[-1],
        )
