# Пересборка памяти MAKO — итог исследования и пилота

Статус: исследование и изолированный пилот. Не production-миграция и не отчёт о готовности живого агента. Production, identities и исходники не менялись; Selectel не включался; массовый backfill не запускался.

## 1. Решение

Рекомендуется **temporal event graph + сильный Raw-RAG baseline + отдельные working/runbook layers**.

- Источник истины — immutable source events с provenance и owner/project scope.
- Temporal claims и связи — производные, исправляемые и пересобираемые представления.
- Raw-history retrieval+reranking — обязательный baseline и fallback.
- CORE владеет исполнением и полномочиями; память только ссылается на run/receipt IDs.
- Mamba/Liquid/новые модели — сменные вычислители после benchmark, не носители семейной истории.

Основания: MCCL, Zep, AMA-Bench, LongMemEval/V2, Raw-RAG, Sleep, MASS и Coinbase fraud agent. Их результаты не считаются нашим воспроизведением.

## 2. Карта данных и разрывов

Проверено read-only:

- Continuous PG: 113 sources, 2601 chunks, 1677 embeddings, 35 pending и 3 spool; 5 sessions.
- Kimi wire: 4357 файлов, 3,85 ГБ, 719470 событий; в continuous попала лишь малая часть сессий.
- Старый Kuzu/Qdrant/PG и новый continuous stack — разные IDs и проекции.
- `reply_to` отсутствует; цепочки decision→action→result покрыты не полностью.
- Production worker остановлен; health это скрывает.
- Локального backup continuous DB не найдено.

Детали: [01-source-inventory](docs/01-source-inventory.md).

## 3. Контракты

Подготовлены схемы в `contracts/`:

- `source-event.schema.json` — участники, права, время события/получения, certainty, digest, CORE ref.
- `claim.schema.json` — утверждение, temporal validity, corrections, extraction versions.
- `evidence-link.schema.json` — связи только по IDs, никакого вывода личности/связи по текстовому сходству.
- `context-snapshot.schema.json` — проверяемый снимок контекста и receipt.
- `migration-record.schema.json` — imported/duplicate/quarantine/error/unavailable без молчаливых пропусков.

Неизвестные авторы/даты/связи остаются unknown. CORE run state в память не копируется.

## 4. Пилот и фактические доказательства

Проверено независимо:

- `/opt/homebrew/bin/python3.12 -m unittest discover -s tests` — **14/14 OK**.
- `/usr/bin/time -l /opt/homebrew/bin/python3.12 runner.py` — все **8/8 checks**.

Сценарий:

1. Старое решение и действие завершены.
2. Новое событие меняет факт и инвалидирует зависимый вывод.
3. Новый CORE action ссылается на новую версию факта.
4. Старый период доступен по `as_of`.
5. Crash после подтверждённого tool effect, restart новым runtime: effect не повторяется, memory/model stages не вызываются повторно.
6. Замена model A→B не меняет canonical data, меняет receipts.

Сравнение:

- no memory: stale prior, неверное действие;
- raw history+reranker: правильное действие и source;
- temporal memory: правильное действие и source.

Доказательство: [pilot-evidence.json](artifacts/pilot-evidence.json).

Важно: synthetic model/tool/policy/transport/verifier — не живой LLM. Настоящие — CORE 0.2.6 `CoreRuntime`, `SQLiteRunStore`, checkpoints и exactly-once recovery. Reranker детерминированный, не embeddings.

## 5. Миграция и rollback

- Стабильные IDs на основе source system + old ID + digest; mapping сохраняется.
- Ledger учитывает каждый источник: imported/duplicate/quarantine/error/unavailable.
- Checkpoints, повторный импорт без дублей, конфликт одинакового ID с другим digest — quarantine.
- Новые события во время переноса покрываются финальной дельтой.
- Оригиналы не удаляются; rollback возвращает raw-history retrieval по ним.
- Массовый перенос запрещён до приёмки bounded real sample.

Детали: [03-migration](docs/03-migration.md).

## 6. Ресурсы

Измерено: runner 0,29–0,45 s, `ru_maxrss=32587776` bytes, 14 tests 0,538 s. Платных вызовов и оставшихся процессов нет.

Это не оценка полного backfill. Для него заданы формула и обязательные измерения: events/s, bytes/s, peak RSS, storage, retries, p50/p95, model calls и стоимость recovery.

Детали: [05-resources](docs/05-resources.md).

## 7. Репозиторий и воспроизведение

- Repository: `/Users/Andreyled/MAKO-Local/kimi-code-m5m/mako-memory-rebuild`
- Branch: `memory/rebuild-pilot`
- HEAD: фиксируется в git после независимой проверки.

```bash
cd /Users/Andreyled/MAKO-Local/kimi-code-m5m/mako-memory-rebuild
/opt/homebrew/bin/python3.12 -m unittest discover -s tests
/opt/homebrew/bin/python3.12 runner.py
/usr/bin/time -l /opt/homebrew/bin/python3.12 runner.py
```

Дополнительные документы:

- [Архитектура](docs/02-architecture.md)
- [Программа приёмки](docs/04-evaluation.md)

## 8. Ограничения и следующие решения для replit

- Нет живого LLM качества, concurrency stress или production latency.
- Нет миграции реальных личных диалогов и старых БД.
- Нет независимого воспроизведения MCCL/Sleep/Raw-RAG; сравниваются концепции, не заявленные цифры.
- Следующий шаг: согласовать общий контракт, затем маленький разрешённый real sample и закрытый runner mini-max-win.
- Production worker restart — отдельное разрешение; одного restart без watchdog недостаточно.
