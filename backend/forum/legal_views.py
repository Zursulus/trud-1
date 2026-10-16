from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from config.network import client_ip
from public_site.legal import current_document
from .legal import ConsentForm, accepted_current
from .models import ConsentRecord, DeletionRequest, Profile
from water.models import User


@login_required(login_url='resident_login')
@never_cache
@require_http_methods(['GET', 'POST'])
def accept_documents(request):
    if request.user.is_staff or request.user.is_superuser:
        raise PermissionDenied
    form = ConsentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if not user.is_active:
                raise PermissionDenied
            if not all(accepted_current(user, kind) for kind in ('consent', 'rules')):
                form.record(request, user)
        request.session.pop('legal_pending', None)
        destination = request.session.pop('legal_next', '')
        if (destination.startswith('/admin/cabinet/')
                and destination != reverse('resident_legal_accept')
                and url_has_allowed_host_and_scheme(destination, allowed_hosts={request.get_host()})):
            return redirect(destination)
        return redirect('resident_legal_profile')
    return render(request, 'forum/legal_accept.html', {'form': form})


@login_required(login_url='resident_login')
@never_cache
@require_http_methods(['GET', 'POST'])
def legal_profile(request):
    if request.user.is_staff or request.user.is_superuser:
        raise PermissionDenied
    if request.method == 'POST':
        if request.POST.get('action') != 'withdraw':
            raise PermissionDenied
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if not user.is_active:
                raise PermissionDenied
            for kind in ('consent', 'binding'):
                last = ConsentRecord.objects.filter(user=user, document_type=kind).order_by('-pk').first()
                document = last.document if last else current_document('consent')
                if document and ((last and last.action == 'accept') or (not last and kind == 'consent')):
                    ConsentRecord.objects.create(user=user, document=document, document_type=kind,
                        version=document.version, action='withdraw', ip=client_ip(request),
                        user_agent=request.META.get('HTTP_USER_AGENT', '')[:1000])
            DeletionRequest.objects.create(user=user)
            user.is_active = False
            user.save(update_fields=['is_active'])
        logout(request)
        return render(request, 'forum/legal_withdrawn.html')
    return render(request, 'forum/legal_profile.html', {
        'profile': Profile.objects.filter(user=request.user).first(),
        'access_requests': request.user.submitted_resident_access_requests.all(),
    })
