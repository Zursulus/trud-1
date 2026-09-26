from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from .models import AppealCategory, ResidentAppeal, ResidentAppealMessage
from .portal import resident_guard
from .portal_permissions import CAP_APPEALS, resolved_access
from .resident_models import (
    APPEAL_ATTACHMENT_EXTENSIONS,
    APPEAL_ATTACHMENT_MAX_BYTES,
    ResidentAppealAttachment,
    ResidentAppealViewState,
)


def validate_appeal_attachment(upload):
    if not upload:
        return upload
    if upload.size > APPEAL_ATTACHMENT_MAX_BYTES:
        raise forms.ValidationError('Файл должен быть не больше 10 МБ.')
    if Path(upload.name).suffix.lower() not in APPEAL_ATTACHMENT_EXTENSIONS:
        raise forms.ValidationError('Разрешены только PDF, JPG и PNG.')
    return upload


class ResidentAppealCreateForm(forms.Form):
    category = forms.ModelChoiceField(label='Тема', queryset=AppealCategory.objects.none())
    subject = forms.CharField(label='Кратко о вопросе', max_length=180)
    message = forms.CharField(label='Сообщение', max_length=5000, widget=forms.Textarea(attrs={'rows': 7}))
    attachment = forms.FileField(
        label='Вложение', required=False,
        help_text='Необязательно. PDF, JPG или PNG до 10 МБ.',
        validators=[validate_appeal_attachment],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = AppealCategory.objects.filter(active=True).order_by('sort_order', 'name')


class ResidentAppealReplyForm(forms.Form):
    body = forms.CharField(label='Ваше сообщение', max_length=5000, widget=forms.Textarea(attrs={'rows': 5}))
    attachment = forms.FileField(
        label='Вложение', required=False,
        help_text='Необязательно. PDF, JPG или PNG до 10 МБ.',
        validators=[validate_appeal_attachment],
    )


def _access(request, account_id):
    access = resolved_access(request.user, account_id, CAP_APPEALS)
    if access is None:
        raise Http404
    return access


def _own_appeal(request, account_id, appeal_id):
    access = _access(request, account_id)
    appeal = get_object_or_404(
        ResidentAppeal.objects.select_related('category', 'responded_by'),
        pk=appeal_id, account=access.account, author=request.user,
    )
    return access, appeal


def _latest_board_event_at(appeal):
    timestamps = []
    if appeal.responded_at and appeal.response.strip():
        timestamps.append(appeal.responded_at)
    latest_message = appeal.board_messages.order_by('-created_at', '-id').first()
    if latest_message:
        timestamps.append(latest_message.created_at)
    return max(timestamps) if timestamps else None


@csrf_protect
@never_cache
def create_appeal(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access(request, account_id)
    form = ResidentAppealCreateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                appeal = ResidentAppeal(
                    account=access.account,
                    author=request.user,
                    category=form.cleaned_data['category'],
                    subject=form.cleaned_data['subject'],
                    message=form.cleaned_data['message'],
                )
                appeal._history_user = request.user
                appeal._change_reason = 'Обращение создано жителем через личный кабинет'
                appeal.save()
                upload = form.cleaned_data.get('attachment')
                if upload:
                    ResidentAppealAttachment.objects.create(
                        appeal=appeal, uploaded_by=request.user, document=upload,
                    )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return HttpResponseRedirect(reverse('resident_appeal', args=[account_id, appeal.pk]))
    return TemplateResponse(request, 'water/portal/appeal_form.html', {
        'account': access.account,
        'access': access,
        'form': form,
        'active_section': 'more',
    })


@csrf_protect
@never_cache
def resident_appeal(request, account_id, appeal_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access, appeal = _own_appeal(request, account_id, appeal_id)
    form = ResidentAppealReplyForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                locked = ResidentAppeal.objects.select_for_update().get(pk=appeal.pk)
                if locked.status in ('resolved', 'closed'):
                    form.add_error(None, 'Обращение уже завершено. Создайте новое, если вопрос остался.')
                else:
                    message = ResidentAppealMessage(
                        appeal=locked, author=request.user, body=form.cleaned_data['body'],
                    )
                    message._history_user = request.user
                    message._change_reason = 'Уточнение отправлено жителем через личный кабинет'
                    message.save()
                    upload = form.cleaned_data.get('attachment')
                    if upload:
                        ResidentAppealAttachment.objects.create(
                            appeal=locked, message=message, uploaded_by=request.user, document=upload,
                        )
                    if locked.status == 'awaiting_resident':
                        locked.status = 'in_progress'
                        locked._history_user = request.user
                        locked._change_reason = 'Житель прислал запрошенное уточнение'
                        locked.save()
                    return HttpResponseRedirect(reverse('resident_appeal', args=[account_id, appeal.pk]))
        except ValidationError as error:
            form.add_error(None, error)

    latest_board_at = _latest_board_event_at(appeal)
    if request.method == 'GET' and latest_board_at:
        ResidentAppealViewState.objects.update_or_create(
            user=request.user,
            appeal=appeal,
            defaults={'last_seen_response_at': latest_board_at},
        )

    attachments = list(
        ResidentAppealAttachment.objects.filter(appeal=appeal)
        .select_related('uploaded_by', 'message', 'board_message')
        .order_by('created_at', 'id')
    )
    initial_attachments = [
        item for item in attachments
        if not item.message_id and not item.board_message_id and not item.is_board_file
    ]
    resident_attachment_map = {}
    board_attachment_map = {}
    legacy_board_attachments = []
    for item in attachments:
        if item.message_id:
            resident_attachment_map.setdefault(item.message_id, []).append(item)
        elif item.board_message_id:
            board_attachment_map.setdefault(item.board_message_id, []).append(item)
        elif item.is_board_file:
            legacy_board_attachments.append(item)

    events = [{
        'kind': 'resident',
        'body': appeal.message,
        'created_at': appeal.opened_at,
        'attachments': initial_attachments,
    }]
    for message in appeal.resident_messages.all():
        events.append({
            'kind': 'resident',
            'body': message.body,
            'created_at': message.created_at,
            'attachments': resident_attachment_map.get(message.pk, []),
        })
    if appeal.response.strip() and appeal.responded_at:
        events.append({
            'kind': 'board',
            'body': appeal.response,
            'created_at': appeal.responded_at,
            'attachments': legacy_board_attachments,
        })
    elif legacy_board_attachments:
        events.append({
            'kind': 'board',
            'body': '',
            'created_at': legacy_board_attachments[0].created_at,
            'attachments': legacy_board_attachments,
        })
    for message in appeal.board_messages.select_related('author').all():
        events.append({
            'kind': 'board',
            'body': message.body,
            'created_at': message.created_at,
            'attachments': board_attachment_map.get(message.pk, []),
            'author': message.author,
        })
    events.sort(key=lambda item: item['created_at'])

    return TemplateResponse(request, 'water/portal/appeal.html', {
        'account': access.account,
        'access': access,
        'appeal': appeal,
        'form': form,
        'events': events,
        'active_section': 'more',
    })


@never_cache
def download_appeal_attachment(request, account_id, appeal_id, attachment_id):
    denied = resident_guard(request)
    if denied:
        return denied
    _access_obj, appeal = _own_appeal(request, account_id, appeal_id)
    attachment = get_object_or_404(
        ResidentAppealAttachment,
        pk=attachment_id,
        appeal=appeal,
    )
    try:
        stream = attachment.document.open('rb')
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error
    response = FileResponse(stream, as_attachment=True, filename=attachment.original_name)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
