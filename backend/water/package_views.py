from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.template.response import TemplateResponse

from .package_imports import inspect_water_package
from .package_writer import import_verified_water_package


class WaterPackageDryRunForm(forms.Form):
    file = forms.FileField(
        label='Подготовленный XLSX-пакет',
        help_text='Файл не сохраняется на сервере.',
    )
    confirm_import = forms.BooleanField(
        label='Подтверждаю импорт в пустую рабочую базу', required=False,
    )


def package_dry_run(request):
    """Admin-only read-only validation of the prepared multi-sheet workbook."""
    form = WaterPackageDryRunForm(request.POST or None, request.FILES or None)
    report = None
    import_result = None
    if request.method == 'POST' and form.is_valid():
        try:
            upload = form.cleaned_data['file']
            if request.POST.get('action') == 'import':
                if not request.user.has_perm('water.change_importbatch'):
                    raise PermissionDenied
                if not form.cleaned_data['confirm_import']:
                    form.add_error('confirm_import', 'Подтвердите импорт.')
                else:
                    import_result = import_verified_water_package(upload)
            else:
                report = inspect_water_package(upload)
        except ValidationError as error:
            form.add_error('file', '; '.join(error.messages))

    context = {
        **admin.site.each_context(request),
        'title': 'Проверка пакета воды без импорта',
        'form': form,
        'report': report,
        'import_result': import_result,
        'can_import': request.user.has_perm('water.change_importbatch'),
    }
    return TemplateResponse(request, 'admin/water/importbatch/package_dry_run.html', context)
