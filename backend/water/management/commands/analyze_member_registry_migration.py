from django.core.management.base import BaseCommand

from water.registry_migration_plan import analyze_member_registry


class Command(BaseCommand):
    help = (
        'Только анализирует закрытый реестр перед возможным переносом в Person/связи/полномочия. '
        'Команда не имеет apply-режима и ничего не записывает в базу.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--show-ids',
            action='store_true',
            help='Показать только внутренние № пользователей по категориям; ПД не выводятся.',
        )

    def handle(self, *args, **options):
        plan = analyze_member_registry()
        for line in plan.lines(show_ids=options['show_ids']):
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS('Анализ завершён. Изменений в БД: 0.'))
