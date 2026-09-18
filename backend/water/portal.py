import hashlib
import secrets
from datetime import timedelta

from django import forms
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from .billing import account_totals
from .models import Account, Charge, Meter, Payment, Reading, ResidentAccess, ResidentInvite, User


class ResidentAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.is_staff:
            raise forms.ValidationError('Сотрудники входят через защищённую административную форму.', code='staff_portal')
        today = timezone.localdate()
        if not user.resident_accesses.filter(starts__lte=today).filter(models_q_active(today)).exists():
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


class ResidentReadingForm(forms.Form):
    date = forms.DateField(label='Дата показания', widget=forms.DateInput(attrs={'type': 'date'}))
    value = forms.DecimalField(label='Показание, м³', min_value=0, max_digits=14, decimal_places=3)
    notes = forms.CharField(label='Примечание', required=False, max_length=500)


def token_digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


@transaction.atomic
def issue_invite(account, email, role, *, actor=None):
    email = email.strip().lower()
    for previous in ResidentInvite.objects.select_for_update().filter(
        account=account, email=email, used_at__isnull=True, revoked=False,
    ):
        previous.revoked = True
        previous._history_user = actor
        previous._change_reason = 'Заменено новым приглашением'
        previous.save()
    raw = secrets.token_urlsafe(32)
    invite = ResidentInvite(
        account=account, email=email, role=role, token_hash=token_digest(raw),
        expires_at=timezone.now() + timedelta(days=7),
    )
    invite._history_user = actor
    invite._change_reason = 'Создание одноразового приглашения'
    invite.save()
    return invite, raw


def active_accesses(user):
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
        raise PermissionDenied
    return None


@never_cache
@csrf_protect
def register_invite(request, token):
    try:
        digest = token_digest(token)
    except UnicodeEncodeError as error:
        raise Http404 from error
    invite = get_object_or_404(ResidentInvite.objects.select_related('account'), token_hash=digest)
    if invite.revoked or invite.used_at or invite.expires_at <= timezone.now():
        return TemplateResponse(request, 'water/portal/invite_invalid.html', status=410)
    if User.objects.filter(email__iexact=invite.email).exists():
        return TemplateResponse(request, 'water/portal/invite_existing.html', {'invite': invite}, status=409)
    form = InviteRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = ResidentInvite.objects.select_for_update().get(pk=invite.pk)
            if locked.revoked or locked.used_at or locked.expires_at <= timezone.now():
                return TemplateResponse(request, 'water/portal/invite_invalid.html', status=410)
            username = invite.email.lower()
            if len(username) > 150 or User.objects.filter(username=username).exists():
                username = f'resident-{secrets.token_hex(12)}'
            user = User.objects.create_user(username=username, email=invite.email, password=form.cleaned_data['password1'])
            access = ResidentAccess(
                user=user, account=invite.account, role=invite.role, starts=timezone.localdate(),
                notes='Создано по одноразовому приглашению',
            )
            access._history_user = user
            access._change_reason = 'Активация приглашения жителем'
            access.save()
            locked.used_at = timezone.now()
            locked._history_user = user
            locked._change_reason = 'Приглашение использовано'
            locked.save()
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return HttpResponseRedirect(reverse('resident_dashboard'))
    return TemplateResponse(request, 'water/portal/register.html', {'form': form, 'invite': invite})


@never_cache
def dashboard(request):
    denied = resident_guard(request)
    if denied:
        return denied
    rows = [{'access': access, 'totals': account_totals(access.account)} for access in active_accesses(request.user)]
    return TemplateResponse(request, 'water/portal/dashboard.html', {'rows': rows})


@never_cache
def resident_account(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = get_object_or_404(active_accesses(request.user), account_id=account_id)
    account = access.account
    context = {
        'account': account, 'access': access, 'totals': account_totals(account),
        'charges': Charge.objects.filter(account=account, status='approved').select_related('period').order_by('-period__starts', '-id'),
        'payments': Payment.objects.filter(account=account, status='confirmed').order_by('-paid_on', '-id'),
        'meters': Meter.objects.filter(account=account, kind='individual').order_by('serial'),
    }
    return TemplateResponse(request, 'water/portal/account.html', context)


@csrf_protect
@never_cache
def submit_reading(request, account_id, meter_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = get_object_or_404(active_accesses(request.user), account_id=account_id)
    meter = get_object_or_404(Meter, pk=meter_id, account=access.account, kind='individual')
    if request.method != 'POST':
        raise Http404
    form = ResidentReadingForm(request.POST)
    if form.is_valid():
        reading = Reading(
            meter=meter, date=form.cleaned_data['date'], value=form.cleaned_data['value'],
            notes=form.cleaned_data['notes'],
        )
        reading._history_user = request.user
        reading._change_reason = 'Показание передано через личный кабинет'
        try:
            reading.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return HttpResponseRedirect(reverse('resident_account', args=[account_id]))
    context = {'account': access.account, 'meter': meter, 'form': form}
    return TemplateResponse(request, 'water/portal/reading.html', context, status=400)
