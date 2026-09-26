from datetime import timedelta

from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from public_site.models import PublicDocument, PublicNews

from .billing import account_totals
from .models import AccountDocument, Charge, Meter, Payment, Reading, ResidentAppeal
from .portal import resident_guard
from .portal_permissions import (
    CAP_APPEALS,
    CAP_DOCUMENTS,
    CAP_FINANCE,
    CAP_VIEW_ACCOUNT,
    resolved_access,
    resolved_accesses,
)
from .resident_models import ResidentAppealViewState
from .resident_numbers import ResidentNumberSlot


def _accesses(user):
    return resolved_accesses(user, CAP_VIEW_ACCOUNT)


def _access_or_404(user, account_id, capability=CAP_VIEW_ACCOUNT):
    access = resolved_access(user, account_id, capability)
    if access is None:
        raise Http404
    return access


def _resident_number(user):
    return ResidentNumberSlot.objects.filter(user=user).values_list('number', flat=True).first()


def _common(request, access=None, section='home'):
    accesses = _accesses(request.user)
    account = access.account if access else None
    return {
        'accesses': accesses,
        'access': access,
        'account': account,
        'active_section': section,
        'resident_number': _resident_number(request.user),
    }


def _latest_reading(account):
    return Reading.objects.filter(
        meter__account=account, meter__kind='individual',
    ).select_related('meter').order_by('-date', '-id').first()


def _appeals_with_unread(user, account):
    appeals = list(
        ResidentAppeal.objects.filter(account=account, author=user)
        .select_related('category')
        .prefetch_related('board_messages')
        .order_by('-opened_at', '-id')
    )
    states = {
        state.appeal_id: state.last_seen_response_at
        for state in ResidentAppealViewState.objects.filter(user=user, appeal__in=appeals)
    }
    for appeal in appeals:
        board_messages = list(appeal.board_messages.all())
        board_message_at = board_messages[-1].created_at if board_messages else None
        legacy_response_at = appeal.responded_at if appeal.response.strip() else None
        candidates = [value for value in (board_message_at, legacy_response_at) if value]
        latest_board_at = max(candidates) if candidates else None
        seen_at = states.get(appeal.pk)
        appeal.portal_unread = bool(
            latest_board_at and (seen_at is None or seen_at < latest_board_at)
        )
        appeal.portal_latest_board_at = latest_board_at
    return appeals


def _home_context(request, access):
    account = access.account
    totals = account_totals(account) if access.can_view_finance else None
    latest_reading = _latest_reading(account)
    latest_charge = None
    if access.can_view_finance:
        latest_charge = Charge.objects.filter(
            account=account, status='approved',
        ).select_related('period').order_by('-period__starts', '-id').first()

    appeals = _appeals_with_unread(request.user, account) if access.can_use_appeals else []
    latest_appeal = appeals[0] if appeals else None
    unread_appeal = next((item for item in appeals if item.portal_unread), None)

    latest_document = None
    latest_news = None
    if access.can_view_documents:
        latest_document = AccountDocument.objects.filter(
            account=account, visible_to_residents=True, published_at__lte=timezone.now(),
        ).select_related('category').order_by('-published_at', '-id').first()
        latest_news = PublicNews.objects.filter(
            is_published=True, public_checked=True, published_on__lte=timezone.localdate(),
        ).order_by('-is_featured', '-published_on', '-id').first()

    attention = []
    if totals is not None and totals['balance'] > 0:
        attention.append({
            'kind': 'money',
            'title': f'К оплате {totals["balance"]} ₽',
            'text': 'Откройте платежи, чтобы посмотреть начисления и историю оплат.',
            'url': reverse('resident_payments', args=[account.pk]),
        })
    if unread_appeal:
        attention.append({
            'kind': 'appeal',
            'title': 'Новый ответ правления',
            'text': unread_appeal.subject,
            'url': reverse('resident_appeal', args=[account.pk, unread_appeal.pk]),
        })
    awaiting = next((item for item in appeals if item.status == 'awaiting_resident'), None)
    if awaiting and awaiting.pk != getattr(unread_appeal, 'pk', None):
        attention.append({
            'kind': 'appeal',
            'title': 'Правление ждёт ваш ответ',
            'text': awaiting.subject,
            'url': reverse('resident_appeal', args=[account.pk, awaiting.pk]),
        })
    if latest_news and latest_news.is_featured:
        attention.append({
            'kind': 'news',
            'title': latest_news.title,
            'text': latest_news.summary,
            'url': reverse('resident_documents', args=[account.pk]),
        })
    if latest_document and latest_document.published_at >= timezone.now() - timedelta(days=7):
        attention.append({
            'kind': 'document',
            'title': 'Новый документ для вашего участка',
            'text': latest_document.title,
            'url': reverse('resident_document', args=[account.pk, latest_document.pk]),
        })

    context = _common(request, access, 'home')
    context.update({
        'totals': totals,
        'latest_reading': latest_reading,
        'latest_charge': latest_charge,
        'latest_appeal': latest_appeal,
        'latest_document': latest_document,
        'latest_news': latest_news,
        'attention': attention[:3],
    })
    return context


@never_cache
def dashboard(request):
    denied = resident_guard(request)
    if denied:
        return denied
    accesses = _accesses(request.user)
    if not accesses:
        return TemplateResponse(request, 'water/portal/no_access.html', status=403)
    return TemplateResponse(request, 'water/portal/dashboard.html', _home_context(request, accesses[0]))


@never_cache
def account_home(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id)
    return TemplateResponse(request, 'water/portal/dashboard.html', _home_context(request, access))


@never_cache
def plots(request):
    denied = resident_guard(request)
    if denied:
        return denied
    rows = []
    for access in _accesses(request.user):
        rows.append({
            'access': access,
            'totals': account_totals(access.account) if access.can_view_finance else None,
            'latest_reading': _latest_reading(access.account),
        })
    context = _common(request, rows[0]['access'] if rows else None, 'plots')
    context['rows'] = rows
    return TemplateResponse(request, 'water/portal/plots.html', context)


@never_cache
def payments(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id, CAP_FINANCE)
    account = access.account
    context = _common(request, access, 'payments')
    context.update({
        'totals': account_totals(account),
        'charges': Charge.objects.filter(account=account, status='approved').select_related('period').order_by('-period__starts', '-id'),
        'payments': Payment.objects.filter(account=account, status='confirmed').order_by('-paid_on', '-id'),
    })
    return TemplateResponse(request, 'water/portal/payments.html', context)


@never_cache
def water(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id)
    meters = Meter.objects.filter(account=access.account, kind='individual').order_by('serial')
    rows = []
    for meter in meters:
        readings = list(Reading.objects.filter(meter=meter).order_by('-date', '-id')[:12])
        rows.append({'meter': meter, 'latest': readings[0] if readings else None, 'readings': readings})
    context = _common(request, access, 'water')
    context['meter_rows'] = rows
    context['today'] = timezone.localdate()
    return TemplateResponse(request, 'water/portal/water.html', context)


@never_cache
def appeals(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id, CAP_APPEALS)
    context = _common(request, access, 'more')
    context['appeals'] = _appeals_with_unread(request.user, access.account)
    return TemplateResponse(request, 'water/portal/appeals.html', context)


@never_cache
def documents(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id, CAP_DOCUMENTS)
    today = timezone.localdate()
    context = _common(request, access, 'more')
    context.update({
        'personal_documents': AccountDocument.objects.filter(
            account=access.account, visible_to_residents=True, published_at__lte=timezone.now(),
        ).select_related('category').order_by('-published_at', '-id'),
        'public_documents': PublicDocument.objects.filter(
            is_published=True, public_checked=True, document_date__lte=today,
        ).select_related('category').order_by('-document_date', '-id')[:60],
        'news': PublicNews.objects.filter(
            is_published=True, public_checked=True, published_on__lte=today,
        ).order_by('-is_featured', '-published_on', '-id')[:20],
    })
    return TemplateResponse(request, 'water/portal/documents.html', context)


@never_cache
def notifications(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id)
    context = _home_context(request, access)
    context['active_section'] = 'more'
    return TemplateResponse(request, 'water/portal/notifications.html', context)


@never_cache
def more(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id)
    return TemplateResponse(request, 'water/portal/more.html', _common(request, access, 'more'))


@never_cache
def profile(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id)
    return TemplateResponse(request, 'water/portal/profile.html', _common(request, access, 'more'))


@never_cache
def security(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id)
    return TemplateResponse(request, 'water/portal/security.html', _common(request, access, 'more'))


@never_cache
def access_help(request):
    return TemplateResponse(request, 'water/portal/access_help.html')
