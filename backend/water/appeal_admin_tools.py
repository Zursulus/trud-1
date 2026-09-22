from pathlib import Path

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
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
    ResidentAppealBoardMessage,
)


class BoardAppealMessageForm(forms.Form):
    body = forms.CharField(label='Сообщение жителю', max_length=5000, widget=forms.Textarea(attrs={'rows': 5}))
    document = forms.FileField(
        label='Вложение', required=False,
        help_text='Необязательно. PDF, JPG или PNG до 10 МБ.',
    )

    def clean_document(self):
        upload = self.cleaned_data.get('document')
        if not upload:
            return upload
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
    can_reply = appeal.status not in ('resolved', 'closed')
    form = BoardAppealMessageForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and not can_reply:
        form.add_error(None, 'Обращение уже завершено. Для нового вопроса нужен новый диалог.')
    elif request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = ResidentAppeal.objects.select_for_update().get(pk=appeal.pk)
            if locked.status in ('resolved', 'closed'):
                form.add_error(None, 'Обращение уже завершено. Обновите страницу.')
            else:
                message = ResidentAppealBoardMessage.objects.create(
                    appeal=locked,
                    author=request.user,
                    body=form.cleaned_data['body'],
                )
                upload = form.cleaned_data.get('document')
                if upload:
                    ResidentAppealAttachment.objects.create(
                        appeal=locked,
                        board_message=message,
                        uploaded_by=request.user,
                        document=upload,
                    )
                if locked.status == 'new':
                    locked.status = 'in_progress'
                    locked._history_user = request.user
                    locked._change_reason = 'Правление начало переписку по обращению'
                    locked.save()
                return HttpResponseRedirect(reverse('admin_appeal_attachments', args=[appeal.pk]))
    attachments = ResidentAppealAttachment.objects.filter(appeal=appeal).select_related(
        'uploaded_by', 'message', 'board_message',
    )
    board_messages = ResidentAppealBoardMessage.objects.filter(appeal=appeal).select_related('author')
    return TemplateResponse(request, 'admin/water/residentappeal/attachments.html', {
        **admin.site.each_context(request),
        'title': f'Переписка по обращению №{appeal.pk}',
        'appeal': appeal,
        'form': form,
        'attachments': attachments,
        'board_messages': board_messages,
        'can_reply': can_reply,
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
