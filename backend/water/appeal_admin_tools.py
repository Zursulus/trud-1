from pathlib import Path

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache

from .models import ResidentAppeal
from .resident_models import (
    APPEAL_ATTACHMENT_EXTENSIONS,
    APPEAL_ATTACHMENT_MAX_BYTES,
    ResidentAppealAttachment,
)


class BoardAppealAttachmentForm(forms.Form):
    document = forms.FileField(label='Файл')

    def clean_document(self):
        upload = self.cleaned_data['document']
        if upload.size > APPEAL_ATTACHMENT_MAX_BYTES:
            raise forms.ValidationError('Файл должен быть не больше 10 МБ.')
        if Path(upload.name).suffix.lower() not in APPEAL_ATTACHMENT_EXTENSIONS:
            raise forms.ValidationError('Разрешены только PDF, JPG и PNG.')
        return upload


@never_cache
def manage_appeal_attachments(request, appeal_id):
    if not request.user.has_perm('water.change_residentappeal'):
        raise PermissionDenied
    appeal = get_object_or_404(ResidentAppeal.objects.select_related('account', 'author'), pk=appeal_id)
    form = BoardAppealAttachmentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        ResidentAppealAttachment.objects.create(
            appeal=appeal,
            uploaded_by=request.user,
            document=form.cleaned_data['document'],
        )
        return HttpResponseRedirect(reverse('admin_appeal_attachments', args=[appeal.pk]))
    attachments = ResidentAppealAttachment.objects.filter(appeal=appeal).select_related('uploaded_by', 'message')
    return TemplateResponse(request, 'admin/water/residentappeal/attachments.html', {
        **admin.site.each_context(request),
        'title': f'Вложения обращения №{appeal.pk}',
        'appeal': appeal,
        'form': form,
        'attachments': attachments,
    })


@never_cache
def download_appeal_attachment(request, attachment_id):
    if not request.user.has_perm('water.view_residentappeal'):
        raise PermissionDenied
    attachment = get_object_or_404(ResidentAppealAttachment.objects.select_related('appeal'), pk=attachment_id)
    try:
        stream = attachment.document.open('rb')
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error
    response = FileResponse(stream, as_attachment=True, filename=attachment.original_name)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
