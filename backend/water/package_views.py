from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.template.response import TemplateResponse

from .package_imports import inspect_water_package


class WaterPackageDryRunForm(forms.Form):
    file = forms.FileField(
        label='Подготовленный XLSX-пакет',
        help_text='Только проверка. Файл не сохраняется, рабочая база не изменяется.',
    )


def package_dry_run(request):
    """Admin-only read-only validation of the prepared multi-sheet workbook."""
    form = WaterPackageDryRunForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == 'POST' and form.is_valid():
        try:
            report = inspect_water_package(form.cleaned_data['file'])
        except ValidationError as error:
            form.add_error('file', '; '.join(error.messages))

    context = {
        **admin.site.each_context(request),
        'title': 'Проверка пакета воды без импорта',
        'form': form,
        'report': report,
    }
    return TemplateResponse(request, 'admin/water/importbatch/package_dry_run.html', context)
