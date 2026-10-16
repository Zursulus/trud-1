from django import forms
from django.core.signing import salted_hmac
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.views.decorators.http import require_http_methods
from forum.legal import BindingConsentForm, accepted_current
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from .access_requests import ResidentAccessRequest


ACCESS_REQUESTS_PER_DAY = 3


class ResidentAccessRequestForm(BindingConsentForm):
    full_name = forms.CharField(label='ФИО как в реестре', max_length=200)
    phone = forms.CharField(label='Телефон', max_length=40, required=False)
    plot_hint = forms.CharField(label='Номер участка', max_length=200)
    last_reading = forms.DecimalField(label='Последнее показание счётчика (для сверки)',
        required=False, min_value=0, max_digits=12, decimal_places=3)
    claimed_role = forms.ChoiceField(
        label='Почему нужен доступ', choices=ResidentAccessRequest.CLAIM_CHOICES,
    )
    message = forms.CharField(
        label='Комментарий', max_length=1000, required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    # Simple honeypot. Normal users never see or fill it; bots still receive the
    # same neutral success response so the field does not become an oracle.
    website = forms.CharField(required=False, widget=forms.HiddenInput)


def _submission_key(request, email):
    remote_addr = request.META.get('REMOTE_ADDR', '')
    day = timezone.localdate().isoformat()
    value = f'{email}|{remote_addr}|{day}'
    return salted_hmac('resident-access-request-v1', value).hexdigest()


@login_required(login_url='resident_login')
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def request_access(request):
    user = request.user
    if not user.is_active or user.is_staff or user.is_superuser:
        raise PermissionDenied
    if not all(accepted_current(user, kind) for kind in ('consent', 'rules')):
        return HttpResponseRedirect(reverse('resident_legal_accept'))
    form = ResidentAccessRequestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            forms.EmailField().clean(user.email)
        except ValidationError:
            form.add_error(None, 'У аккаунта нет корректного email. Обратитесь в правление для уточнения данных.')
            return TemplateResponse(request, 'water/portal/access_request.html', {'form': form})
        key = _submission_key(request, user.email)
        with transaction.atomic():
            from .models import User
            user = User.objects.select_for_update().get(pk=user.pk)
            if not user.is_active or not all(accepted_current(user, kind) for kind in ('consent', 'rules')):
                raise PermissionDenied
            recent_count = ResidentAccessRequest.objects.filter(
                requester_user=user, submitted_at__date=timezone.localdate()).count()
            if form.cleaned_data.get('website'):
                return HttpResponseRedirect(reverse('resident_access_request_sent'))
            if recent_count >= ACCESS_REQUESTS_PER_DAY:
                form.add_error(None, 'Сегодня уже отправлено три заявки. Дождитесь решения правления.')
            else:
                form.record(request, user)
                ResidentAccessRequest.objects.create(
                    full_name=form.cleaned_data['full_name'], email=user.email,
                    phone=form.cleaned_data['phone'], plot_hint=form.cleaned_data['plot_hint'],
                    claimed_role=form.cleaned_data['claimed_role'], message=form.cleaned_data['message'],
                    last_reading=form.cleaned_data['last_reading'],
                    submission_key=key, requester_user=user,
                    consent_record=user.consent_records.filter(document_type='binding').latest('pk'),
                )
                return HttpResponseRedirect(reverse('resident_access_request_sent'))
    return TemplateResponse(request, 'water/portal/access_request.html', {'form': form})


@login_required(login_url='resident_login')
@never_cache
def request_access_sent(request):
    return TemplateResponse(request, 'water/portal/access_request_sent.html', {
        'access_requests': request.user.submitted_resident_access_requests.all(),
    })
