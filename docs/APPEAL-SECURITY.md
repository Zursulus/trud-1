[Reading 78 lines from start (total: 78 lines, 0 remaining)]

# Безопасность вложений обращений

## Политика

В обращениях принимаются только:

- PDF;
- JPG/JPEG;
- PNG;
- DOCX;
- XLSX.

Максимальный размер одного файла — 10 МБ. Старые Office-форматы (`.doc`, `.xls`), macro-enabled (`.docm`, `.xlsm`), архивы и исполняемые/активные форматы не принимаются.

Проверка выполняется до сохранения в закрытое хранилище:

1. allowlist расширения;
2. фактическая сигнатура/структура формата;
3. защита OOXML от path traversal, шифрованных элементов, zip-bomb, VBA, ActiveX и embedded objects;
4. ClamAV через `clamdscan`;
5. только после успешной проверки — сохранение вложения.

Production работает fail-closed: если обязательный антивирус недоступен, новый файл не принимается.

## Антиспам и квоты

По умолчанию:

- 5 успешных сообщений/обращений в минуту на пользователя;
- 3 новых обращения за 10 минут;
- 12 POST-попыток в минуту на пользователя + хэш сетевого источника;
- 50 МБ вложений на пользователя в сутки;
- 100 МБ вложений на одно обращение.

Пороговые значения задаются переменными окружения `TRUD_APPEAL_*`. Ограничение не отключает учётную запись автоматически: событие фиксируется для администратора, а пользователю предлагается повторить позже.

## Сигналы безопасности

В `SecurityAlert` фиксируются только данные, необходимые для расследования: внутренний ID пользователя, лицевой счёт/обращение, тип события, имя и размер файла, технический код причины и HMAC-хэш сетевого источника. Текст сообщений и сырой IP в журнал не копируются.

Открытые сигналы отображаются Администратору ТСН в `/work/` и в очереди `/work/security/`. Повтор одинакового события дедуплицируется в 15-минутном окне, чтобы не создавать лавину уведомлений.

## Production prerequisite

Production не запускает локальный clamd: приложение использует `clamdscan` и приватный loopback TCP endpoint `127.0.0.1:3310`, который через ограниченный reverse-SSH tunnel ведёт к clamd на sandbox. clamd не публикуется наружу.

Перед deploy обязательно:
- sandbox `clamav-daemon`, `clamav-freshclam` и tunnel active;
- официальные CVD/CLD signature databases присутствуют и актуальны;
- на production `127.0.0.1:3310` отвечает clamd;
- clean `INSTREAM` возвращает OK, EICAR `INSTREAM` возвращает FOUND;
- приложение запускает `clamdscan --stream`, чтобы remote clamd не пытался открыть production-local pathname.

Scanner-loss должен оставаться fail-closed: при недоступном tunnel новый upload отклоняется, а malware-scan requirement не отключается.

Production defaults:

```text
TRUD_APPEAL_MALWARE_SCAN_REQUIRED=1
TRUD_APPEAL_CLAMDSCAN_PATH=/usr/bin/clamdscan
TRUD_APPEAL_CLAMDSCAN_TIMEOUT=20
```

Перед запуском новой версии:

1. сверить точный deployed SHA и target SHA;
2. проверить backup/rollback readiness;
3. установить/проверить ClamAV;
4. выполнить `python manage.py check --deploy` — `water.E901` должен отсутствовать;
5. применить migration `0029_security_alert`;
6. выполнить `python manage.py setup_roles`;
7. controlled deploy;
8. проверить service/marker/HTTP;
9. проверить безопасный DOCX/XLSX upload, запрещённый формат и появление/закрытие тестового security alert;
10. только после этого считать production acceptance завершённым.

Production не используется для отладки вредоносных файлов. Антивирусный smoke выполняется контролируемым тестовым артефактом без сохранения в репозиторий или пользовательское хранилище.

[executed on device: sandbox (2ce8fd8f-c8b1-4737-95b3-20fa4189189e)]