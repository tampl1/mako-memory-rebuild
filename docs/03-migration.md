# План переноса и обратного переключения

Массовый backfill не запускается до приёмки пилота. Оригиналы не удаляются и не переписываются.

## 1. Идентификаторы

- `event_id` — детерминированный UUID/hex на основе SHA-256 от `schema_version + source_system + source_event_id + content_digest`.
- `source_ref = (source_system, source_event_id, revision)` хранится отдельно от нового ID.
- Одинаковый `(source_system, source_event_id)` с другим digest → `quarantine/conflict`, а не автоматическое слияние.
- `source_aliases` сохраняет старые IDs и причины дедупликации.
- Связи actor/recipient/reply/decision/action/result создаются только из явных старых IDs, никогда из текстового сходства.

## 2. Migration ledger

Каждый исходный объект получает ровно один терминальный статус:

- `imported` — digest и mapping подтверждены;
- `duplicate` — точная предыдущая версия уже импортирована, alias записан;
- `quarantine` — конфликт, невалидная схема, неизвестный формат или нарушение scope;
- `error` — временная ошибка чтения/записи с повторяемым checkpoint;
- `unavailable` — источник известен, но недоступен/не расшифрован/не предоставлен.

Поля: `source_system`, `old_id`, `new_event_id`, `status`, `reason`, `digest`, `attempt`, `checkpoint`, `updated_at`. Молчаливых пропусков нет.

## 3. Этапы

1. **Read-only inventory:** объёмы, scopes, ID coverage, временные диапазоны, gaps.
2. **Small copy pilot:** только synthetic/обезличенные связанные диалоги нескольких агентов; отдельный SQLite/каталог.
3. **Schema rehearsal:** импорт, повторный импорт, conflict/quarantine, as_of recall, restart.
4. **Bounded real-sample rehearsal:** маленький разрешённый набор после отдельного решения; сравнение counts/hashes/scopes.
5. **Shadow import:** новый модуль пишет только в новое хранилище, production не меняется.
6. **Final delta:** импорт событий, появившихся после high-watermark предыдущего этапа.
7. **Acceptance:** ledger сходится с исходным количеством; нет неучтённых объектов; rollback проверен.

## 4. Checkpoints и повторный импорт

- После каждой ограниченной партии сохраняются `source_system`, `old_id/high_watermark`, `batch_digest`, `next_checkpoint`.
- Повторный запуск читает ledger и не создаёт новых событий/effects.
- Checkpoint не считается завершённым до фиксации ledger и всех связанных projection jobs.
- Старые проекции не удаляются; новые строятся заново из source events.

## 5. Новые события во время переноса

Текущие hooks продолжают писать в старый контур. Дельта-процесс читает новые `created_at/old_id` и добавляет их в ledger. Финальная дельта обязательна перед переключением read traffic.

## 6. Обратное переключение

- `memory_read_mode=legacy|shadow|new` — управляемый config, а не второй claimant.
- При отказе нового контура возвращается raw-history retrieval по оригиналам.
- Новые проекции можно отключить без удаления источников; старые БД и IDs остаются доступными.
- Переключение обратно не удаляет новые события: их source refs и mapping позволяют replay в старый контур после отдельной приёмки.

## 7. Критерии остановки

- Расхождение counts или digest;
- Неизвестный owner/project/visibility;
- Повторный эффект после restart;
- Исчезновение старого периода по `as_of`;
- Неучтённый источник;
- Отсутствие rollback evidence.
