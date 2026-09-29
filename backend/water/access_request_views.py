from django import forms
from django.core.signing import salted_hmac
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from .access_requests import ResidentAccessRequest
from .portal_permissions import has_any_portal_access


ACCESS_REQUESTS_PER_DAY = 3


class ResidentAccessRequestForm(forms.Form):
    full_name = forms.CharField(label='Как к вам обращаться', max_length=200)
    email = forms.EmailField(label='Электронная почта')
    phone = forms.CharField(label='Телефон', max_length=40, required=False)
    plot_hint = forms.CharField(label='Участок / адрес', max_length=200)
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

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


def _submission_key(request, email):
    remote_addr = request.META.get('REMOTE_ADDR', '')
    day = timezone.localdate().isoformat()
    value = f'{email}|{remote_addr}|{day}'
    return salted_hmac('resident-access-request-v1', value).hexdigest()


def _current_resident_user(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not user.is_active or user.is_staff:
        return None
    if not has_any_portal_access(user):
        return None
    return user


@csrf_protect
@never_cache
def request_access(request):
    requester_user = _current_resident_user(request)
    initial = {'email': requester_user.email} if requester_user and requester_user.email else None
    form = ResidentAccessRequestForm(request.POST or None, initial=initial)
    if requester_user:
        form.fields['email'].help_text = 'Email нужен для связи; заявка уже привязана к текущему кабинету.'
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        key = _submission_key(request, email)
        is_honeypot = bool(form.cleaned_data.get('website'))
        recent_count = ResidentAccessRequest.objects.filter(
            submission_key=key,
            submitted_at__date=timezone.localdate(),
        ).count()
        if not is_honeypot and recent_count < ACCESS_REQUESTS_PER_DAY:
            ResidentAccessRequest.objects.create(
                full_name=form.cleaned_data['full_name'],
                email=email,
                phone=form.cleaned_data['phone'],
                plot_hint=form.cleaned_data['plot_hint'],
                claimed_role=form.cleaned_data['claimed_role'],
                message=form.cleaned_data['message'],
                submission_key=key,
                requester_user=requester_user,
            )
        return HttpResponseRedirect(reverse('resident_access_request_sent'))
    return TemplateResponse(request, 'water/portal/access_request.html', {'form': form})


@never_cache
def request_access_sent(request):
    return TemplateResponse(request, 'water/portal/access_request_sent.html')
