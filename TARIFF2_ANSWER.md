# Исправление node_excel

- `backend/water/tariff_recalculation.py`: `build_comparison` берёт знаменатель
  из `calculate_water_balance(start, end).nodes[*].confirmed_known`.
- `_preview_node_charges`: несохранённый Tariff для каждой WaterGroup →
  `calculate_period(preview=True, group_tariffs=...)`, без записи начислений.
- `backend/water/billing.py`: `calculate_period`, `calculate_group_losses`,
  `resolve_tariff` поддерживают предпросмотр с общей логикой начислений.
  Сохраняются действующие политики, округление и приоритет индивидуальных тарифов.
- При confirmed_known=0 ставка отсутствует; узел и его счета требуют проверки.
- `sources` учитывает изменения BillingPolicy, BillingAssignment и Tariff.
- Обновлены подписи в `tariff_views.py`, `tariff_recalculation_detail.html`
  и раздел «Методика» в `docs/claude/tariff-recalc/README.md`.

Формула: ROUND_HALF_UP(ставка ВК × вход узла / confirmed_known, 0.01).
Узел без гидранта: 109.34 × 489 / 386 = 138.52 ₽/м³.
Узел с гидрантом: 109.34 × 471 / 354 = 145.48 ₽/м³.

Тесты: `backend/water/test_tariff_recalculation.py`, 9 новых регрессий.
Целевой прогон: 50 тестов, OK; полный `water config`: 678, OK (6 skipped).
`ops/tests`: 71, OK. check, makemigrations --check --dry-run,
проверка shell-синтаксиса и git diff --check: OK. Миграций нет.
Все данные синтетические; предпросмотр не меняет тарифы, начисления и историю.

Хеш нового коммита: отсутствует — .git доступен только для чтения.
`git add` завершился кодом 128: невозможно создать .git/index.lock (Read-only file system).
Текущий HEAD: 8d702fb; ветка claude/water-tariff-20261021. Push не выполнялся.
Сообщение с заданными trailers: /home/chatgpt-remote/work/tmp/tariff2-commit-message.txt.
После снятия ограничения нужен коммит с user.name=Claude,
user.email=noreply@anthropic.com и этим сообщением.

Спорных решений по формуле нет; использован confirmed_known, включая известную
часть неполного баланса. Старые черновики следует создать заново.
Проверки выполнены локально на SQLite; реальная база и браузер не использовались.
