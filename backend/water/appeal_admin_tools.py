from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache

from .access_resolver import can_any
from .appeal_security import (
    APPEAL_ATTACHMENT_HELP,
    record_form_upload_rejection,
    validate_appeal_attachment,
)
from .appeal_workflow import send_board_reply
from .models import ResidentAppeal
from .resident_models import ResidentAppealAttachment, ResidentAppealBoardMessage


class BoardAppealMessageForm(forms.Form):
    body = forms.CharField(label='Сообщение жителю', max_length=5000, widget=forms.Textarea(attrs={'rows': 5}))
    document = forms.FileField(
        label='Вложение', required=False,
        help_text=f'Необязательно. {APPEAL_ATTACHMENT_HELP}',
        validators=[validate_appeal_attachment],
        widget=forms.ClearableFileInput(attrs={'accept': '.pdf,.jpg,.jpeg,.png,.docx,.xlsx'}),
    )


@never_cache
def manage_appeal_attachments(request, appeal_id):
    if not request.user.is_staff or not can_any(request.user, 'appeals.reply'):
        raise PermissionDenied
    appeal = get_object_or_404(ResidentAppeal.objects.select_related('account', 'author'), pk=appeal_id)
    can_reply = appeal.status not in ('resolved', 'closed')
    form = BoardAppealMessageForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and not can_reply:
        form.add_error(None, 'Обращение уже завершено. Для нового вопроса нужен новый диалог.')
    elif request.method == 'POST':
        valid = form.is_valid()
        if not valid:
            record_form_upload_rejection(
                form=form,
                field_name='document',
                request=request,
                actor=request.user,
                account=appeal.account,
                appeal=appeal,
            )
        else:
            try:
                send_board_reply(
                    appeal_id=appeal.pk,
                    actor=request.user,
                    body=form.cleaned_data['body'],
                    document=form.cleaned_data.get('document'),
                )
            except ValidationError as error:
                form.add_error(None, '; '.join(error.messages))
            else:
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
    if not request.user.is_staff or not can_any(request.user, 'appeals.attachment.view'):
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
