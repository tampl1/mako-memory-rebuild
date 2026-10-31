# mako-memory-rebuild — isolated memory pilot

Ветка `memory/rebuild-pilot`. Изолированный пилот единого контракта памяти +
проверяемый сценарий на реальном CORE 0.2.6 (`CoreRuntime` + `SQLiteRunStore`
из `attached-assets-price-review/repo/scripts/backend/mnemosyne-core`, подключение
только через локальный `sys.path` в `src/adapters.py`). Только stdlib Python
3.11+. Только synthetic multi-agent dialogs, без ПДн.

## Запуск

Закреплённый CORE: Attached-Assets `1cbe2e7c0d258f02c19105c15f7d540e89398a93`,
каталог `scripts/backend/mnemosyne-core`. Путь можно передать через
`MAKO_CORE_DIR`; сами файлы CORE пилот не изменяет.

```bash
export MAKO_CORE_DIR=/path/to/Attached-Assets/scripts/backend/mnemosyne-core
python3 -m unittest discover -s tests
python3 runner.py
python3 negative_runner.py
```

Negative acceptance дополнительно проверяет два порядка исправления, as_of,
недоступный retrieval, потерянные/подменённые evidence links, чужого owner,
отозванный grant, смену embedding-generation, испорченный checkpoint и
injection-документы. Цитата допускает анализ, но не даёт прав, tool calls,
эффектов, записи в память или обучение. `raw_history` — raw-контроль без живого
reranker; детерминированная сортировка не выдаётся за модельный rerank.

Все БД/процессы — только во временных каталогах с cleanup; платных/сетевых
вызовов нет.

## Структура

- `contracts/*.schema.json` — source-event, claim, evidence-link,
  context-snapshot, migration-record. Обязательны actor/recipients/owner/
  project/session, occurred_at+ingested_at, valid/invalid time, corrections,
  evidence/provenance, certainty (explicit/deterministic/inferred), model
  versions, source digest. Unknown не выдумывается (actor=null, occurred_at
  обязателен). Связи только по IDs. CORE run/receipt — только ссылка.
- `src/temporal_store.py` — SQLite temporal store: immutable events/claims,
  инкрементальная invalidation зависимых claims (direct evidence links +
  derived_from chains), recall с provenance, raw-history + детерминированный
  reranker baseline, идемпотентные migration_records (UNIQUE).
- `src/adapters.py` — synthetic model/tool/policy/transport/verifier +
  MemoryContext-обёртка; верификатор требует consistency (решение модели ==
  исполненный effect) и актуальности fact_ref.
- `src/pilot.py` — сценарий: решение → изменение факта → invalidation → новое
  действие, привязанное к новой версии факта; crash после tool execution и
  restart новым runtime (effect ровно один, checkpoint сохраняет); смена
  модели A→B без изменения данных.
- `runner.py` — JSON evidence: environment (включая дайджесты файлов CORE),
  expected/actual, receipts, checks, limitations, duration, ru_maxrss.

## Ограничения

Synthetic model/tool — не живой LLM; настоящие — CORE runtime, store и
exactly-once recovery. Reranker — детерминированный keyword-scoring, не
эмбеддинги. Одиночный SQLite, без concurrency-stress.
