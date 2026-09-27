from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from .appeal_security import (
    APPEAL_ATTACHMENT_HELP,
    enforce_resident_submission_limits,
    record_form_upload_rejection,
    register_resident_submission_attempt,
    validate_appeal_attachment,
)
from .appeal_workflow import appeal_conversation_events, latest_board_event_at
from .models import AppealCategory, ResidentAppeal, ResidentAppealMessage
from .portal import resident_guard
from .portal_permissions import CAP_APPEALS, resolved_access
from .resident_models import ResidentAppealAttachment, ResidentAppealViewState


FILE_ACCEPT = '.pdf,.jpg,.jpeg,.png,.docx,.xlsx'


class ResidentAppealCreateForm(forms.Form):
    category = forms.ModelChoiceField(label='Тема', queryset=AppealCategory.objects.none())
    subject = forms.CharField(label='Кратко о вопросе', max_length=180)
    message = forms.CharField(label='Сообщение', max_length=5000, widget=forms.Textarea(attrs={'rows': 7}))
    attachment = forms.FileField(
        label='Вложение', required=False,
        help_text=f'Необязательно. {APPEAL_ATTACHMENT_HELP}',
        validators=[validate_appeal_attachment],
        widget=forms.ClearableFileInput(attrs={'accept': FILE_ACCEPT}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = AppealCategory.objects.filter(active=True).order_by('sort_order', 'name')


class ResidentAppealReplyForm(forms.Form):
    body = forms.CharField(label='Ваше сообщение', max_length=5000, widget=forms.Textarea(attrs={'rows': 5}))
    attachment = forms.FileField(
        label='Вложение', required=False,
        help_text=f'Необязательно. {APPEAL_ATTACHMENT_HELP}',
        validators=[validate_appeal_attachment],
        widget=forms.ClearableFileInput(attrs={'accept': FILE_ACCEPT}),
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


def _prepare_resident_post(request, form, *, account, appeal=None, creating=False):
    """Validate once, count every POST attempt, then apply persistent quotas."""
    valid = form.is_valid()
    try:
        register_resident_submission_attempt(request, request.user)
    except ValidationError as error:
        form.add_error(None, error)
        return False

    if not valid:
        record_form_upload_rejection(
            form=form,
            field_name='attachment',
            request=request,
            actor=request.user,
            account=account,
            appeal=appeal,
        )
        return False

    upload = form.cleaned_data.get('attachment')
    try:
        enforce_resident_submission_limits(
            request=request,
            user=request.user,
            account=account,
            appeal=appeal,
            upload=upload,
            creating=creating,
        )
    except ValidationError as error:
        form.add_error(None, error)
        return False
    return True


@csrf_protect
@never_cache
def create_appeal(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access(request, account_id)
    form = ResidentAppealCreateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and _prepare_resident_post(
        request, form, account=access.account, creating=True,
    ):
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
    if request.method == 'POST' and _prepare_resident_post(
        request, form, account=access.account, appeal=appeal,
    ):
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

    latest_board_at = latest_board_event_at(appeal)
    if request.method == 'GET' and latest_board_at:
        ResidentAppealViewState.objects.update_or_create(
            user=request.user,
            appeal=appeal,
            defaults={'last_seen_response_at': latest_board_at},
        )

    return TemplateResponse(request, 'water/portal/appeal.html', {
        'account': access.account,
        'access': access,
        'appeal': appeal,
        'form': form,
        'events': appeal_conversation_events(appeal),
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
