from datetime import timedelta

from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from public_site.models import PublicDocument, PublicNews

from .billing import account_totals
from .models import AccountDocument, Charge, Meter, Payment, Reading, ResidentAppeal
from .portal import active_accesses, resident_guard


def _accesses(user):
    return list(active_accesses(user).order_by('account__plot', 'account__number', 'account_id'))


def _access_or_404(user, account_id):
    from django.shortcuts import get_object_or_404
    return get_object_or_404(active_accesses(user), account_id=account_id)


def _common(request, access=None, section='home'):
    accesses = _accesses(request.user)
    account = access.account if access else None
    return {
        'accesses': accesses,
        'access': access,
        'account': account,
        'active_section': section,
        'is_board_member': request.user.groups.filter(name__in=('Правление', 'board_member')).exists(),
    }


def _latest_reading(account):
    return Reading.objects.filter(
        meter__account=account, meter__kind='individual',
    ).select_related('meter').order_by('-date', '-id').first()


def _home_context(request, access):
    account = access.account
    totals = account_totals(account)
    latest_reading = _latest_reading(account)
    latest_charge = Charge.objects.filter(
        account=account, status='approved',
    ).select_related('period').order_by('-period__starts', '-id').first()
    latest_appeal = ResidentAppeal.objects.filter(
        account=account, author=request.user,
    ).select_related('category').order_by('-opened_at', '-id').first()
    latest_document = AccountDocument.objects.filter(
        account=account, visible_to_residents=True, published_at__lte=timezone.now(),
    ).select_related('category').order_by('-published_at', '-id').first()
    latest_news = PublicNews.objects.filter(
        is_published=True, public_checked=True, published_on__lte=timezone.localdate(),
    ).order_by('-is_featured', '-published_on', '-id').first()

    attention = []
    balance = totals['balance']
    if balance > 0:
        attention.append({
            'kind': 'money',
            'title': f'К оплате {balance} ₽',
            'text': 'Откройте платежи, чтобы посмотреть начисления и историю оплат.',
            'url': reverse('resident_payments', args=[account.pk]),
        })
    awaiting = ResidentAppeal.objects.filter(
        account=account, author=request.user, status='awaiting_resident',
    ).order_by('-opened_at').first()
    if awaiting:
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
            'totals': account_totals(access.account),
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
    access = _access_or_404(request.user, account_id)
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
    access = _access_or_404(request.user, account_id)
    context = _common(request, access, 'more')
    context['appeals'] = ResidentAppeal.objects.filter(
        account=access.account, author=request.user,
    ).select_related('category').order_by('-opened_at', '-id')
    return TemplateResponse(request, 'water/portal/appeals.html', context)


@never_cache
def documents(request, account_id):
    denied = resident_guard(request)
    if denied:
        return denied
    access = _access_or_404(request.user, account_id)
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
