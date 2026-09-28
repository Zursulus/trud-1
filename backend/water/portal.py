import hashlib
import secrets
from datetime import timedelta

from django import forms
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from .billing import account_totals
from .models import (
    Account, AccountDocument, AppealCategory, Charge, ControllerReadingSubmission, Membership, Meter, Payment, Reading,
    ResidentAccess, ResidentAppeal, ResidentAppealMessage, ResidentInvite,
    ResidentPasswordReset, User,
)
from .portal_permissions import (
    CAP_APPEALS,
    CAP_DOCUMENTS,
    CAP_FINANCE,
    CAP_SUBMIT_WATER,
    CAP_VIEW_ACCOUNT,
    PortalGrant,
    has_any_portal_access,
    resolved_access,
    resolved_accesses,
)
from .resident_models import ResidentIdentity


class ResidentAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.is_staff:
            raise forms.ValidationError('Сотрудники входят через защищённую административную форму.', code='staff_portal')
        if not has_any_portal_access(user):
            next_path = ''
            if self.request is not None:
                next_path = self.request.POST.get('next') or self.request.GET.get('next') or ''
            if not _invite_login_allowed(user, next_path):
                raise forms.ValidationError('Нет действующего доступа к лицевому счёту.', code='no_access')


class InviteRegistrationForm(forms.Form):
    password1 = forms.CharField(label='Пароль', strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label='Повторите пароль', strip=False, widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        if data.get('password1') != data.get('password2'):
            self.add_error('password2', 'Пароли не совпадают.')
        if data.get('password1'):
            validate_password(data['password1'])
        return data


class NewResidentPasswordForm(InviteRegistrationForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = forms.Form.clean(self)
        if data.get('password1') != data.get('password2'):
            self.add_error('password2', 'Пароли не совпадают.')
        if data.get('password1'):
            validate_password(data['password1'], user=self.user)
        return data


class ResidentReadingForm(forms.Form):
    date = forms.DateField(label='Дата показания', widget=forms.DateInput(attrs={'type': 'date'}))
    value = forms.DecimalField(label='Показание, м³', min_value=0, max_digits=14, decimal_places=3)
    notes = forms.CharField(label='Примечание', required=False, max_length=500)


class ResidentAppealForm(forms.Form):
    category = forms.ModelChoiceField(label='Тема', queryset=AppealCategory.objects.none())
    subject = forms.CharField(label='Кратко о вопросе', max_length=180)
    message = forms.CharField(label='Сообщение', max_length=5000, widget=forms.Textarea(attrs={'rows': 7}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = AppealCategory.objects.filter(active=True).order_by('sort_order', 'name')


class ResidentAppealMessageForm(forms.Form):
    body = forms.CharField(label='Ваше уточнение', max_length=5000, widget=forms.Textarea(attrs={'rows': 5}))


def token_digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def _invite_login_allowed(user, next_path):
    prefix = '/admin/cabinet/invite/'
    if not next_path.startswith(prefix):
        return False
    token = next_path[len(prefix):].strip('/')
    if not token or '/' in token:
        return False
    try:
        digest = token_digest(token)
    except UnicodeEncodeError:
        return False
    return ResidentInvite.objects.filter(
        token_hash=digest, email__iexact=user.email, used_at__isnull=True, revoked=False,
        expires_at__gt=timezone.now(), account__archived=False,
    ).exists()


def _revoke_previous_invites(account, email, *, actor=None):
    for previous in ResidentInvite.objects.select_for_update().filter(
        account=account, email=email, used_at__isnull=True, revoked=False,
    ):
        previous.revoked = True
        previous._history_user = actor
        previous._change_reason = 'Заменено новым приглашением'
        previous.save()


@transaction.atomic
def issue_invite(account, email, role, *, actor=None):
    if account.archived:
        raise ValidationError('Для архивного лицевого счёта нельзя создавать приглашение.')
    email = email.strip().lower()
    _revoke_previous_invites(account, email, actor=actor)
    raw = secrets.token_urlsafe(32)
    invite = ResidentInvite(
        account=account, email=email, role=role, token_hash=token_digest(raw),
        expires_at=timezone.now() + timedelta(days=7),
    )
    invite._history_user = actor
    invite._change_reason = 'Создание одноразового приглашения'
    invite.save()
    return invite, raw


@transaction.atomic
def issue_granular_invite(
    account, person, email, basis, *, actor, can_view_account=True,
    can_view_finance=False, can_submit_water=False, can_view_documents=False,
    can_use_appeals=False, can_represent=False,
):
    if account.archived:
        raise ValidationError('Для архивного лицевого счёта нельзя создавать приглашение.')
    if person.archived:
        raise ValidationError('Для архивной карточки человека нельзя создавать приглашение.')
    if not actor or not actor.is_staff:
        raise ValidationError('Точечное приглашение должен подтвердить сотрудник.')
    today = timezone.localdate()
    if PortalGrant.objects.filter(person=person, account=account, starts__lte=today).filter(
        Q(ends__isnull=True) | Q(ends__gt=today)
    ).exists():
        raise ValidationError('Для этого жителя уже действует явный доступ к выбранному счёту.')
    email = email.strip().lower()
    _revoke_previous_invites(account, email, actor=actor)
    raw = secrets.token_urlsafe(32)
    invite = ResidentInvite(
        account=account, person=person, email=email, role='', basis=basis, verified_by=actor,
        can_view_account=can_view_account, can_view_finance=can_view_finance,
        can_submit_water=can_submit_water, can_view_documents=can_view_documents,
        can_use_appeals=can_use_appeals, can_represent=can_represent,
        token_hash=token_digest(raw), expires_at=timezone.now() + timedelta(days=7),
    )
    invite._history_user = actor
    invite._change_reason = 'Создание точечного приглашения жителя'
    invite.save()
    return invite, raw


def _activate_granular_invite(invite, user):
    identity = ResidentIdentity.objects.select_for_update().filter(user=user).first()
    if identity and identity.person_id != invite.person_id:
        raise ValidationError('Кабинет уже связан с другой проверенной карточкой человека.')
    other_identity = ResidentIdentity.objects.select_for_update().filter(person=invite.person).exclude(user=user).first()
    if other_identity:
        raise ValidationError('Проверенная карточка человека уже связана с другим кабинетом.')
    if identity is None:
        identity = ResidentIdentity(
            user=user, person=invite.person, verified_by=invite.verified_by, basis=invite.basis,
        )
        identity._history_user = invite.verified_by
        identity._change_reason = 'Личность подтверждена по точечному приглашению'
        identity.save()

    grant = PortalGrant(
        person=invite.person, account=invite.account, starts=timezone.localdate(),
        can_view_account=invite.can_view_account, can_view_finance=invite.can_view_finance,
        can_submit_water=invite.can_submit_water, can_view_documents=invite.can_view_documents,
        can_use_appeals=invite.can_use_appeals, can_represent=invite.can_represent,
        basis=invite.basis, verified_by=invite.verified_by,
        notes='Создано по точечному одноразовому приглашению',
    )
    grant._history_user = invite.verified_by
    grant._change_reason = 'Активация точечного приглашения жителем'
    grant.save()
    invite.used_at = timezone.now()
    invite._history_user = user
    invite._change_reason = 'Точечное приглашение использовано'
    invite.save()
    return grant


def password_reset_allowed(user):
    return bool(
        user.is_active
        and not user.is_superuser
        and (has_any_portal_access(user) or user.has_perm('water.use_controller_workspace'))
    )


@transaction.atomic
def issue_password_reset(user, *, actor=None):
    if not password_reset_allowed(user):
        raise ValidationError('Сброс пароля разрешён только активному жителю, старшему линии или контролёру.')
    for previous in ResidentPasswordReset.objects.select_for_update().filter(
        user=user, used_at__isnull=True, revoked=False,
    ):
        previous.revoked = True
        previous._history_user = actor
        previous._change_reason = 'Заменено новой ссылкой восстановления'
        previous.save()
    raw = secrets.token_urlsafe(32)
    reset = ResidentPasswordReset(
        user=user, token_hash=token_digest(raw), expires_at=timezone.now() + timedelta(hours=24),
    )
    reset._history_user = actor
    reset._change_reason = 'Создание одноразовой ссылки восстановления'
    reset.save()
    return reset, raw


def active_accesses(user):
    """Legacy ResidentAccess queryset kept only for transitional invite logic/tests."""
    today = timezone.localdate()
    return user.resident_accesses.filter(starts__lte=today).filter(
        models_q_active(today), account__archived=False,
    ).select_related('account')


def models_q_active(today):
    from django.db.models import Q
    return Q(ends__isnull=True) | Q(ends__gt=today)


def resident_guard(request):
    if not request.user.is_authenticated:
        return HttpResponseRedirect(f'{reverse("resident_login")}?next={request.path}')
    if request.user.is_staff:
        return TemplateResponse(request, 'water/portal/staff_forbidden.html', status=403)
    return None


def _resolved_or_404(user, account_id, capability=CAP_VIEW_ACCOUNT):
    access = resolved_access(user, account_id, capability)
    if access is None:
        raise Http404
    return access


@never_cache
@csrf_protect
def register_invite(request, token):
    try:
        digest = token_digest(token)
    except UnicodeEncodeError as error:
        raise Http404 from error
    invite = get_object_or_404(
        ResidentInvite.objects.select_related('account', 'person', 'verified_by'),
        token_hash=digest,
    )
    if invite.revoked or invite.used_at or invite.expires_at <= timezone.now() or invite.account.archived:
        return TemplateResponse(request, 'water/portal/invite_invalid.html', status=410)
    if invite.is_granular and (invite.person.archived or not invite.verified_by_id):
        return TemplateResponse(request, 'water/portal/invite_invalid.html', status=410)

    email_users = User.objects.filter(email__iexact=invite.email).order_by('pk')
    if email_users.exists():
        existing_users = email_users.filter(is_staff=False, is_active=True)
        if email_users.count() != 1 or existing_users.count() != 1:
            return TemplateResponse(request, 'water/portal/invite_existing.html', {
                'invite': invite, 'ambiguous': True,
            }, status=409)
        existing = existing_users.first()
        if not request.user.is_authenticated:
            return TemplateResponse(request, 'water/portal/invite_existing.html', {'invite': invite})
        if request.user.is_staff or request.user.pk != existing.pk:
            return TemplateResponse(request, 'water/portal/invite_existing.html', {
                'invite': invite, 'wrong_user': True,
            }, status=403)
        if request.method == 'POST':
            try:
                with transaction.atomic():
                    locked = ResidentInvite.objects.select_for_update(of=('self',)).select_related(
                        'account', 'person', 'verified_by',
                    ).get(pk=invite.pk)
                    user = User.objects.select_for_update().get(pk=existing.pk)
                    if locked.revoked or locked.used_at or locked.expires_at <= timezone.now() or locked.account.archived:
                        return TemplateResponse(request, 'water/portal/invite_invalid.html', status=410)
                    if user.is_staff or not user.is_active or user.email.lower() != locked.email.lower():
                        return TemplateResponse(request, 'water/portal/invite_existing.html', {
                            'invite': locked, 'ambiguous': True,
                        }, status=409)
                    if locked.is_granular:
                        _activate_granular_invite(locked, user)
                    else:
                        today = timezone.localdate()
                        active = ResidentAccess.objects.filter(
                            user=user, account=locked.account, starts__lte=today,
                        ).filter(models_q_active(today)).exists()
                        if not active:
                            access = ResidentAccess(
                                user=user, account=locked.account, role=locked.role, starts=today,
                                notes='Дополнительный счёт подключён по одноразовому приглашению',
                            )
                            access._history_user = user
                            access._change_reason = 'Подключение дополнительного счёта жителем'
                            access.save()
                        locked.used_at = timezone.now()
                        locked._history_user = user
                        locked._change_reason = 'Приглашение использовано существующим жителем'
                        locked.save()
            except ValidationError:
                return TemplateResponse(request, 'water/portal/invite_existing.html', {
                    'invite': invite, 'ambiguous': True,
                }, status=409)
            return HttpResponseRedirect(reverse('resident_account', args=[invite.account_id]))
        return TemplateResponse(request, 'water/portal/invite_existing.html', {
            'invite': invite, 'ready': True,
        })

    form = InviteRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                locked = ResidentInvite.objects.select_for_update(of=('self',)).select_related(
                    'account', 'person', 'verified_by',
                ).get(pk=invite.pk)
                if locked.revoked or locked.used_at or locked.expires_at <= timezone.now() or locked.account.archived:
                    return TemplateResponse(request, 'water/portal/invite_invalid.html', status=410)
                username = locked.email.lower()
                if len(username) > 150 or User.objects.filter(username=username).exists():
                    username = f'resident-{secrets.token_hex(12)}'
                user = User.objects.create_user(
                    username=username, email=locked.email, password=form.cleaned_data['password1'],
                )
                if locked.is_granular:
                    _activate_granular_invite(locked, user)
                else:
                    access = ResidentAccess(
                        user=user, account=locked.account, role=locked.role, starts=timezone.localdate(),
                        notes='Создано по одноразовому приглашению',
                    )
                    access._history_user = user
                    access._change_reason = 'Активация приглашения жителем'
                    access.save()
                    locked.used_at = timezone.now()
                    locked._history_user = user
                    locked._change_reason = 'Приглашение использовано'
                    locked.save()
        except ValidationError:
            return TemplateResponse(request, 'water/portal/invite_existing.html', {
                'invite': invite, 'ambiguous': True,
            }, status=409)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return HttpResponseRedirect(reverse('resident_dashboard'))
    return TemplateResponse(request, 'water/portal/register.html', {'form': form, 'invite': invite})


@never_cache
@csrf_protect
def reset_password(request, token):
    try:
        digest = token_digest(token)
    except UnicodeEncodeError as error:
        raise Http404 from error
    reset = get_object_or_404(ResidentPasswordReset.objects.select_related('user'), token_hash=digest)
    if reset.revoked or reset.used_at or reset.expires_at <= timezone.now() or not password_reset_allowed(reset.user):
        return TemplateResponse(request, 'water/portal/reset_invalid.html', status=410)
    form = NewResidentPasswordForm(request.POST or None, user=reset.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = ResidentPasswordReset.objects.select_for_update().select_related('user').get(pk=reset.pk)
            if locked.revoked or locked.used_at or locked.expires_at <= timezone.now():
                return TemplateResponse(request, 'water/portal/reset_invalid.html', status=410)
            user = User.objects.select_for_update().get(pk=locked.user_id)
            if not password_reset_allowed(user):
                return TemplateResponse(request, 'water/portal/reset_invalid.html', status=410)
            user.set_password(form.cleaned_data['password1'])
            user.save(update_fields=['password'])
            locked.used_at = timezone.now()
            locked._history_user = user
            locked._change_reason = 'Пароль восстановлен жителем'
            locked.save()
        if user.is_staff:
            return HttpResponseRedirect(reverse('admin:login'))
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return HttpResponseRedirect(reverse('resident_dashboard'))
    return TemplateResponse(request, 'water/portal/reset_password.html', {'form': form})


@never_cache
@csrf_protect
def change_password(request):
    denied = resident_guard(request)
    if denied:
        return denied
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        return HttpResponseRedirect(reverse('resident_dashboard'))
    return TemplateResponse(request, 'water/portal/change_password.html', {'form': form})


@never_cache
def dashboard(request):
    denied = resident_guard(request)
    if denied:
        return denied
    rows = []
    for access in resolved_accesses(request.user, CAP_VIEW_ACCOUNT):
        rows.append({
            'access': access,
            'totals': account_totals(access.account) if access.can_view_finance else None,
        })
    return TemplateResponse(request, 'water/portal/dashboard.html', {'rows': rows})


@never_cache
def resident_account(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _resolved_or_404(request.user, account_id)
    account = access.account
    context = {
        'account': account,
        'access': access,
        'totals': account_totals(account) if access.can_view_finance else None,
        'charges': Charge.objects.filter(account=account, status='approved').select_related('period').order_by('-period__starts', '-id') if access.can_view_finance else Charge.objects.none(),
        'payments': Payment.objects.filter(account=account, status='confirmed').order_by('-paid_on', '-id') if access.can_view_finance else Payment.objects.none(),
        'meters': Meter.objects.filter(account=account, kind='individual').order_by('serial'),
        'appeals': ResidentAppeal.objects.filter(account=account, author=request.user).select_related('category') if access.can_use_appeals else ResidentAppeal.objects.none(),
        'documents': AccountDocument.objects.filter(
            account=account, visible_to_residents=True, published_at__lte=timezone.now(),
        ).select_related('category') if access.can_view_documents else AccountDocument.objects.none(),
    }
    return TemplateResponse(request, 'water/portal/account.html', context)


@csrf_protect
@never_cache
def submit_reading(request, account_id, meter_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _resolved_or_404(request.user, account_id, CAP_SUBMIT_WATER)
    meter = get_object_or_404(Meter, pk=meter_id, account=access.account, kind='individual')
    if request.method != 'POST':
        raise Http404
    form = ResidentReadingForm(request.POST)
    if form.is_valid():
        observed_on = form.cleaned_data['date']
        line_member = Membership.objects.filter(
            account=access.account, starts__lte=observed_on,
        ).filter(Q(ends__isnull=True) | Q(ends__gt=observed_on)).exists()
        try:
            with transaction.atomic():
                Meter.objects.select_for_update().get(pk=meter.pk)
                submission = ControllerReadingSubmission.objects.select_for_update().filter(
                    meter=meter, date=observed_on, submitted_by=request.user,
                    source=ControllerReadingSubmission.SOURCE_RESIDENT, status='pending',
                ).order_by('-submitted_at', '-id').first()
                if submission is None:
                    submission = ControllerReadingSubmission(
                        meter=meter, date=observed_on, submitted_by=request.user,
                        source=ControllerReadingSubmission.SOURCE_RESIDENT,
                    )
                    reason = 'Показание передано жителем на проверку'
                else:
                    reason = 'Житель уточнил ожидающее проверки показание'
                submission.value = form.cleaned_data['value']
                submission.notes = form.cleaned_data['notes']
                submission.submitted_at = timezone.now()
                submission.line_review_status = (
                    ControllerReadingSubmission.LINE_REVIEW_PENDING
                    if line_member else ControllerReadingSubmission.LINE_REVIEW_NOT_REQUIRED
                )
                submission.line_reviewed_by = None
                submission.line_reviewed_at = None
                submission.line_review_comment = ''
                submission._history_user = request.user
                submission._change_reason = reason
                submission.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return HttpResponseRedirect(reverse('resident_account', args=[account_id]))
    context = {'account': access.account, 'access': access, 'meter': meter, 'form': form}
    return TemplateResponse(request, 'water/portal/reading.html', context, status=400)


@csrf_protect
@never_cache
def create_appeal(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _resolved_or_404(request.user, account_id, CAP_APPEALS)
    form = ResidentAppealForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        appeal = ResidentAppeal(
            account=access.account, author=request.user, category=form.cleaned_data['category'],
            subject=form.cleaned_data['subject'], message=form.cleaned_data['message'],
        )
        appeal._history_user = request.user
        appeal._change_reason = 'Обращение создано жителем через личный кабинет'
        appeal.save()
        return HttpResponseRedirect(reverse('resident_appeal', args=[account_id, appeal.pk]))
    return TemplateResponse(request, 'water/portal/appeal_form.html', {
        'account': access.account, 'access': access, 'form': form,
    })


@never_cache
def resident_appeal(request, account_id, appeal_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _resolved_or_404(request.user, account_id, CAP_APPEALS)
    appeal = get_object_or_404(
        ResidentAppeal.objects.select_related('category'), pk=appeal_id,
        account=access.account, author=request.user,
    )
    form = ResidentAppealMessageForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
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
                if locked.status == 'awaiting_resident':
                    locked.status = 'in_progress'
                    locked._history_user = request.user
                    locked._change_reason = 'Житель прислал запрошенное уточнение'
                    locked.save()
                return HttpResponseRedirect(reverse('resident_appeal', args=[account_id, appeal.pk]))
    context = {
        'account': access.account, 'access': access, 'appeal': appeal, 'form': form,
        'resident_messages': appeal.resident_messages.all(),
    }
    return TemplateResponse(request, 'water/portal/appeal.html', context)


@never_cache
def download_document(request, account_id, document_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _resolved_or_404(request.user, account_id, CAP_DOCUMENTS)
    document = get_object_or_404(
        AccountDocument, pk=document_id, account=access.account,
        visible_to_residents=True, published_at__lte=timezone.now(),
    )
    try:
        stream = document.document.open('rb')
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error
    response = FileResponse(stream, as_attachment=True, filename=document.original_name)
    response['Cache-Control'] = 'private, no-store'
    return response
