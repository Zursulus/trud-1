from django.core.management.base import BaseCommand
from django.db import transaction
from water.models import Account


class Command(BaseCommand):
    help = 'Создать 305 пустых карточек. Повторный запуск не меняет заполненные данные.'

    @transaction.atomic
    def handle(self, *args, **options):
        created = 0
        for slot in range(1, 306):
            _, is_new = Account.objects.get_or_create(seed_slot=slot)
            created += int(is_new)
        self.stdout.write(self.style.SUCCESS(f'Создано: {created}. Начальных карточек: 305.'))
