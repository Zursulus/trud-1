from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

from water.package_writer import import_verified_water_package


class Command(BaseCommand):
    help = 'Импортировать проверенный XLSX-пакет в пустой water-реестр.'

    def add_arguments(self, parser):
        parser.add_argument('file', type=Path)
        parser.add_argument('--confirm', required=True)

    def handle(self, *args, **options):
        if options['confirm'] != 'IMPORT-VERIFIED-WATER-PACKAGE':
            raise CommandError('Неверное подтверждение импорта.')
        path = options['file'].resolve()
        if not path.is_file():
            raise CommandError(f'Файл не найден: {path}')
        upload = SimpleUploadedFile(path.name, path.read_bytes())
        result = import_verified_water_package(upload)
        self.stdout.write(self.style.SUCCESS('Импорт завершён.'))
        for key, value in result.items():
            self.stdout.write(f'{key}: {value}')
