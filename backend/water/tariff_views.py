import csv
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .admin import spreadsheet_safe, validation_text
from .models import BillingPeriod
from .tariff_models import TariffRecalculation
from .tariff_recalculation import audit, create_recalculation, decide_recalculation, require_permission


class RecalculationForm(forms.ModelForm):
    class Meta:
        model = TariffRecalculation
        fields = ('period', 'rate', 'starts', 'ends', 'basis', 'document', 'loss_distribution')
        widgets = {
            'starts': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'ends': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'rate': forms.NumberInput(attrs={'step': '0.01', 'inputmode': 'decimal'}),
        }
        help_texts = {
            'starts': 'Тариф должен действовать на весь выбранный период.',
            'ends': 'Необязательно; дата окончания не включается.',
            'document': 'Необязательно: PDF, изображение или офисный документ, до 10 МБ. Доступ только сотрудникам с правом пересчёта.',
            'loss_distribution': 'По умолчанию: ставка ВК × вход ОСН / подтверждённый расход абонентов узла.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['period'].queryset = BillingPeriod.objects.exclude(status='closed')
        self.fields['loss_distribution'].choices = [
            choice for choice in self.fields['loss_distribution'].choices if choice[0] != 'manual'
        ]
        self.fields['rate'].localize = True  # Accept both 109,34 and 109.34.


class DecisionForm(forms.Form):
    confirm = forms.BooleanField(label='Подтверждаю проверку сравнения и основания')
    version = forms.IntegerField(widget=forms.HiddenInput)


def context(request, **extra):
    return {**admin.site.each_context(request), 'title': 'Тариф водоканала → пересчёт периода', **extra}


@require_http_methods(['GET', 'POST'])
def home(request):
    require_permission(request.user)
    form = RecalculationForm(request.POST or None, request.FILES or None)
    if request.method == 'POST':
        require_permission(request.user, 'add')
        if form.is_valid():
            try:
                item = create_recalculation(actor=request.user, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, validation_text(error))
            else:
                return redirect('water_tariff_recalculation_detail', recalc_id=item.pk)
    return TemplateResponse(request, 'admin/water/tariff_recalculation_form.html', context(
        request, form=form, recalculations=TariffRecalculation.objects.select_related('period', 'created_by')[:40],
        can_create=request.user.has_perm('water.add_tariffrecalculation'),
    ))


def _selected_rows(item, request):
    rows = item.result['accounts']
    query = request.GET.get('q', '').strip().casefold()
    node = request.GET.get('node', '')
    state = request.GET.get('state', '')
    return [row for row in rows if
            (not query or query in ' '.join(str(row[key]) for key in ('number', 'plot', 'group', 'node')).casefold())
            and (not node or str(row['node_id']) == node)
            and (state != 'review' or row['reason']) and (state != 'ready' or not row['reason'])]


def _csv(item, request, rows):
    audit(item, request.user, 'Экспорт сравнения пересчёта CSV')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="tariff-recalculation-{item.pk}.csv"'
    response['Cache-Control'] = 'no-store'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')

    def write(values):
        # Numeric columns stay numeric in Russian Excel; text cells are escaped.
        writer.writerow([str(value).replace('.', ',') if isinstance(value, Decimal)
                         else spreadsheet_safe('' if value is None else value) for value in values])

    def number(value):
        return Decimal(value) if value is not None else None

    write(['Пересчёт', item.pk, 'Основание', item.basis, 'Ставка ВК', item.rate,
           'Методика', item.get_loss_distribution_display()])
    write(['Узел', 'Вход ОСН, м³', 'Абоненты, м³', 'Потери, м³', 'Ставка узла', 'Формула', 'Было ₽', 'Стало ₽', 'Разница ₽'])
    for node in item.result['nodes']:
        write([node['name'], *[number(node[key]) for key in ('input', 'confirmed', 'loss', 'rate')],
               node['formula'], *[number(node[key]) for key in ('old', 'new', 'delta')]])
    write(['Линия', 'Узел', 'Объём линии, м³', 'Абоненты, м³', 'Потери линии, м³', 'Было ₽', 'Стало ₽', 'Разница ₽'])
    for group in item.result['groups']:
        write([group['name'], group['node'], *[number(group[key]) for key in ('input', 'confirmed', 'loss', 'old', 'new', 'delta')]])
    write(['Счёт', 'Участок', 'Узел', 'Линия', 'Расход м³', 'Ставка', 'Было ₽', 'Стало ₽', 'Разница ₽', 'Разница %', 'Ручная проверка'])
    for row in rows:
        write([row['number'], row['plot'], row['node'], row['group'],
               *[number(row[key]) for key in ('volume', 'rate', 'old', 'new', 'delta', 'percent')], row['reason']])
    return response


@require_http_methods(['GET', 'POST'])
def detail(request, recalc_id):
    require_permission(request.user)
    item = get_object_or_404(TariffRecalculation.objects.select_related('period', 'created_by', 'decided_by'), pk=recalc_id)
    decision = DecisionForm(initial={'version': item.version})
    if request.method == 'POST':
        action = request.POST.get('action')
        require_permission(request.user, 'approve' if action == 'approve' else 'add')
        decision = DecisionForm(request.POST)
        if action not in ('approve', 'cancel'):
            decision.add_error(None, 'Неизвестное действие.')
        elif action == 'cancel' or decision.is_valid():
            try:
                if str(item.version) != request.POST.get('version'):
                    raise ValidationError('Версия пересчёта изменилась. Обновите страницу.')
                decide_recalculation(recalc_id=item.pk, actor=request.user, approve=action == 'approve')
            except ValidationError as error:
                decision.add_error(None, validation_text(error))
            else:
                messages.success(request, 'Пересчёт утверждён: корректировки записаны.' if action == 'approve'
                                 else 'Черновик отменён. Начисления не изменены.')
                return redirect('water_tariff_recalculation_detail', recalc_id=item.pk)
    rows = _selected_rows(item, request)
    if request.method == 'GET' and request.GET.get('export') == 'csv':
        return _csv(item, request, rows)
    return TemplateResponse(request, 'admin/water/tariff_recalculation_detail.html', context(
        request, item=item, result=item.result, rows=[row for row in rows if not row['reason']],
        review_rows=[row for row in rows if row['reason']], decision=decision,
        can_approve=request.user.has_perm('water.approve_tariffrecalculation'),
        can_cancel=request.user.has_perm('water.add_tariffrecalculation'),
        csv_url=reverse('water_tariff_recalculation_detail', args=[item.pk]) + '?' +
                request.GET.copy().urlencode() + '&export=csv',
    ))


@require_http_methods(['GET'])
def document(request, recalc_id):
    require_permission(request.user)
    item = get_object_or_404(TariffRecalculation, pk=recalc_id)
    if not item.document:
        raise Http404
    audit(item, request.user, 'Скачивание основания пересчёта')
    response = FileResponse(item.document.open('rb'), as_attachment=True, filename='tariff-basis' +
                            item.document.name[item.document.name.rfind('.'):])
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
