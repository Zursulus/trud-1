"""Authenticated email binding, separate from allauth's resident login stages.

Uses the pinned allauth code process, mail templates and EmailAddress validation.
Never calls login(), changes an authentication backend or clears a login stage.
"""
import time

from allauth.account.adapter import get_adapter
from allauth.account.internal.flows.email_verification_by_code import EmailVerificationProcess
from allauth.account.models import EmailAddress
from allauth.core import ratelimit
from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import ProfileForm
from .models import Profile

SESSION_KEY = 'profile_email_binding'
UNAVAILABLE = 'Этот адрес недоступен для привязки.'


def available_email(user, email):
    if get_user_model().objects.filter(email__iexact=email).exclude(pk=user.pk).exists() or (
        EmailAddress.objects.filter(email__iexact=email).exclude(user=user).exists()
    ):
        raise ValidationError(UNAVAILABLE)
    return email


class BindingForm(forms.Form):
    email = forms.EmailField(label='Почта', max_length=254, widget=forms.EmailInput(attrs={
        'autocomplete': 'email', 'inputmode': 'email',
        'autocapitalize': 'off', 'spellcheck': 'false',
    }))
    password = forms.CharField(label='Текущий пароль', required=False, widget=forms.PasswordInput(attrs={
        'autocomplete': 'current-password',
    }))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if not user.has_usable_password():
            del self.fields['password']

    def clean_email(self):
        return available_email(self.user, self.cleaned_data['email'].lower())

    def clean(self):
        data = super().clean()
        if self.user.has_usable_password() and not self.user.check_password(data.get('password', '')):
            self.add_error('password', 'Проверьте текущий пароль.')
        return data


class CodeForm(forms.Form):
    code = forms.CharField(label='Код из письма', max_length=16, widget=forms.TextInput(attrs={
        'autocomplete': 'one-time-code', 'inputmode': 'numeric',
    }))


class BindingProcess(EmailVerificationProcess):
    def persist(self):
        self.request.session[SESSION_KEY] = self.state

    def abort(self):
        self.request.session.pop(SESSION_KEY, None)

    @classmethod
    def start(cls, request, target):
        user = request.user
        # Passwordless sessions must first prove control of their current address.
        prove_current = not user.has_usable_password() and bool(user.email)
        state = cls.initial_state(user, user.email if prove_current else target)
        state.update(target=target, original=user.email, auth_hash=user.get_session_auth_hash(),
                     phase='current' if prove_current else 'target')
        process = cls(request, state, user=user)
        process.generate_code()
        process.send()
        if process.did_send:
            process.persist()
        return process.did_send

    @classmethod
    def load(cls, request):
        state = request.session.get(SESSION_KEY)
        if not state:
            return None
        process = cls(request, state, user=request.user)
        if (str(state['user_id']) != str(request.user.pk)
                or state['original'] != request.user.email
                or not constant_time_compare(state['auth_hash'], request.user.get_session_auth_hash())
                or not process.is_valid()):
            process.abort()
            return None
        return process


def _staff_session_guard(request):
    if not request.user.is_active:
        raise PermissionDenied
    if request.user.is_staff and not admin.site.has_permission(request):
        # Preserve the configured mandatory MFA boundary for sensitive staff writes.
        return admin.site.login(request)
    return None


@login_required(login_url='resident_login')
@never_cache
@require_http_methods(['GET', 'POST'])
def profile(request):
    denied = _staff_session_guard(request)
    if denied:
        return denied
    obj = Profile.objects.filter(user=request.user).first() or Profile(user=request.user)
    form = ProfileForm(request.POST if request.method == 'POST' else None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Ник сохранён.')
        return redirect('personal_profile')
    return render(request, 'forum/personal_profile.html', {
        'form': form, 'email_verified': EmailAddress.objects.filter(
            user=request.user, email__iexact=request.user.email, verified=True,
        ).exists(), 'title': 'Профиль',
    })


@login_required(login_url='resident_login')
@never_cache
@require_http_methods(['GET', 'POST'])
def email(request):
    denied = _staff_session_guard(request)
    if denied:
        return denied
    process = BindingProcess.load(request)
    form = CodeForm(request.POST if request.method == 'POST' else None) if process else BindingForm(
        request.POST if request.method == 'POST' else None, user=request.user,
        initial={'email': request.user.email},
    )
    if request.method == 'POST':
        if request.POST.get('action') == 'cancel':
            request.session.pop(SESSION_KEY, None)
            return redirect('profile_email')
        if not ratelimit.consume(request, action='profile_email', user=request.user):
            form.add_error(None, 'Слишком много попыток. Попробуйте через пять минут.')
        elif form.is_valid():
            if process:
                if not constant_time_compare(form.cleaned_data['code'], process.code):
                    process.record_invalid_attempt()
                    form.add_error('code', 'Неверный код. После трёх ошибок запросите новый.')
                elif process.state['phase'] == 'current':
                    process.state.update(phase='target', email=process.state['target'],
                                         at=time.time(), failed_attempts=0)
                    process.__dict__.pop('email_address', None)
                    process.generate_code()
                    process.send()
                    if process.did_send:
                        process.persist()
                        return redirect('profile_email')
                    process.abort()
                    form.add_error(None, 'Отправка временно ограничена. Запросите код позже.')
                else:
                    try:
                        with transaction.atomic():
                            user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
                            if user.email != process.state['original'] or not constant_time_compare(
                                user.get_session_auth_hash(), process.state['auth_hash'],
                            ):
                                raise ValidationError('Данные изменились. Запросите новый код.')
                            target = available_email(user, process.state['target'])
                            address = EmailAddress.objects.filter(user=user, email__iexact=target).first()
                            if address is None:
                                address = EmailAddress(user=user, email=target)
                            if not address.verified and not get_adapter(request).confirm_email(request, address):
                                raise ValidationError(UNAVAILABLE)
                            address.set_as_primary()
                        process.abort()
                        messages.success(request, 'Почта подтверждена и сохранена.')
                        return redirect('personal_profile')
                    except (ValidationError, IntegrityError):
                        process.abort()
                        form.add_error(None, 'Не удалось привязать адрес. Запросите новый код или обратитесь в правление.')
            else:
                if BindingProcess.start(request, form.cleaned_data['email']):
                    return redirect('profile_email')
                form.add_error(None, 'Отправка временно ограничена. Запросите код позже.')
    return render(request, 'forum/profile_email.html', {
        'form': form, 'pending': process is not None,
        'confirm_current': process is not None and process.state['phase'] == 'current',
        'title': 'Почта',
    })
