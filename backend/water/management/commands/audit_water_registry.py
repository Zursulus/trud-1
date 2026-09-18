from django.core.management.base import BaseCommand

from water.models import (
    Account,
    GroupConsumption,
    LandPlot,
    Membership,
    Meter,
    Person,
    PlotRelation,
    Reading,
    SupplyNode,
    WaterGroup,
)


class Command(BaseCommand):
    help = 'Read-only audit of the current water registry before staged import.'

    def handle(self, *args, **options):
        models = (
            ('Лицевые счета', Account),
            ('Участки', LandPlot),
            ('Люди', Person),
            ('Связи людей и участков', PlotRelation),
            ('Узлы', SupplyNode),
            ('Группы', WaterGroup),
            ('Состав групп', Membership),
            ('Счётчики', Meter),
            ('Показания', Reading),
            ('Расходы групп', GroupConsumption),
        )
        self.stdout.write('=== ТЕКУЩАЯ БАЗА: ТОЛЬКО ЧТЕНИЕ ===')
        for label, model in models:
            self.stdout.write(f'{label}: {model.objects.count()}')

        self.stdout.write('\n=== КАНДИДАТЫ НА ТЕСТОВЫЕ ЗАПИСИ ===')
        candidates = []
        for obj in Account.objects.all().order_by('pk'):
            text = ' | '.join(filter(None, [obj.number or '', obj.plot, obj.contact_name, obj.phone, obj.notes]))
            if any(marker in text.casefold() for marker in ('test', 'тест')):
                candidates.append(('Account', obj.pk, text))
        for obj in LandPlot.objects.all().order_by('pk'):
            text = ' | '.join(filter(None, [obj.label, obj.address, obj.cadastral_number or '', obj.notes]))
            if any(marker in text.casefold() for marker in ('test', 'тест')):
                candidates.append(('LandPlot', obj.pk, text))
        for obj in Person.objects.all().order_by('pk'):
            text = ' | '.join(filter(None, [obj.full_name, obj.phone, obj.email, obj.notes]))
            if any(marker in text.casefold() for marker in ('test', 'тест')):
                candidates.append(('Person', obj.pk, text))
        for obj in Meter.objects.select_related('node').all().order_by('pk'):
            text = ' | '.join(filter(None, [obj.serial, obj.notes]))
            if any(marker in text.casefold() for marker in ('test', 'тест')):
                candidates.append(('Meter', obj.pk, text))

        if not candidates:
            self.stdout.write('По явным маркерам TEST/ТЕСТ кандидатов не найдено.')
        else:
            for model, pk, text in candidates:
                self.stdout.write(f'{model} id={pk}: {text}')

        self.stdout.write('\nНичего не изменено и не удалено.')
