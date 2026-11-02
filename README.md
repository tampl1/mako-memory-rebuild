# mako-memory-rebuild — isolated memory pilot

Ветка `memory/rebuild-pilot`. Изолированный пилот единого контракта памяти +
проверяемый сценарий на реальном CORE 0.2.6 (`CoreRuntime` + `SQLiteRunStore`
из `attached-assets-price-review/repo/scripts/backend/mnemosyne-core`, подключение
только через локальный `sys.path` в `src/adapters.py`). Только stdlib Python
3.11+. Только synthetic multi-agent dialogs, без ПДн.

## Integration handoff

Ветка `memory/bridge-package-20261101`.

- `integration/memory-bridge-manifest.json` — единый contract для MiniMax:
  source/version/as_of/owner/tenant/project/grant_revision/embedding_generation/digest,
  runtime model digest/capability и одинаковые arms/scorer/inputs.
- `integration/history-shadow-manifest.json` — read-only shadow только метаданных
  трёх реальных wire-событий, без содержимого диалогов; rollback и negative controls.
- `integration/quality-audit.json` — twin-sft и Guard v1/v2: artifact, lineage,
  license, baseline/heldout/retention. Guard v2 не продвигается по дате файла.
- `docs/sleep-feasibility.md` — отличие MoE/Hope Sleep от LoRA/replay; это не
  репликация статьи и не запуск обучения.

## Frozen evaluation и shadow migration

Ветка `memory/frozen-evaluation-20261101`.

```bash
MAKO_CORE_DIR=/path/to/core-2fa /opt/homebrew/bin/python3.12 frozen_eval.py
/opt/homebrew/bin/python3.12 shadow_migration.py
```

Двадцать заранее заданных сценариев для no-memory/raw/memory-module с единым
scorer/inputs: факт/as_of/contradiction/missing source/revocation/owner/tenant/
project/embedding-generation/injection/incomplete history/model swap/child v1→v2.
Результаты разделены PASS/FAIL/SKIP/UNKNOWN и goal DELIVERED/NOT_DELIVERED.
Scorer `frozen-goal-safety/2.0` отделяет case execution от фактических
proposed/attempted_call/admitted_call/recorded_effect. Safe abstention не является
доставленной целью. Два порядка поступления и два synthetic model receipts —
отдельные эксперименты. Модельных вызовов нет; улучшение качества не заявляется.
Shadow migration — только metadata,
3/3 записи, digests, unreadable=unresolved и реальный rollback.

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

Точные MiniMax controls закреплены из Attached-Assets
`fa495f74b9e4147b8cc7594cf521797ae6f83a98`, путь
`artifacts/memory-pilot-20261031/` (22 файла). Перед запуском проверяются все
21 хэш из MANIFEST; живой текст не пересказывается и не генерируется заново.

```bash
python3 minimax_runner.py
```

T15 — анализ цитаты без действия; T14 — попытка вызова без допуска и эффекта;
T17 — разрешённый владельцем synthetic вызов с одним admitted call и одним
recorded effect. Это harmless counting tool, не платёж и не внешний эффект.

Совместимость с CORE `2fa094082324af15b2c0fa7ad38c95dab92d7147` проверяется
отдельно, без перезаписи прежней квитанции:

```bash
MAKO_CORE_DIR=/path/to/core-2fa MAKO_CORE_GIT_HEAD=2fa094082324af15b2c0fa7ad38c95dab92d7147 \
  /opt/homebrew/bin/python3.12 runner.py > artifacts/pilot-evidence-core-2fa.json
```

Все БД/процессы — только во временных каталогах с cleanup; платных/сетевых
вызовов нет.

## Live M1M retrieval

Ветка `memory/live-retrieval-20261031`. Реальный `bge-m3` (1024 dims) и
generative reranker `qwen3.5:9b` через SSH-туннель на M1M; это не dedicated
cross-encoder. Полные digest и каждый вызов записываются в evidence. Лимит —
64 вызова и 30 минут, жёсткий budget в `CallBudget`.

```bash
MAKO_CORE_DIR=/path/to/core-2fa /opt/homebrew/bin/python3.12 live_runner.py
```

Сценарий включает CORE old→correction→invalidation→new, as_of, owner/grant,
retrieval outage, evidence tamper, injection и restart. Reranker получает только
query/document, не expected source. Raw и rerank сравниваются на одних входах.
Live-процессы M5M были остановлены после двух smoke, показавших GPU; рабочий
прогон выполнен только на выделенном M1M.

## Offline replay и child context

Ветка `memory/offline-child-context-20261031` не повторяет live runner.

```bash
/opt/homebrew/bin/python3.12 offline_replay.py
MAKO_CORE_DIR=/path/to/core-2fa /opt/homebrew/bin/python3.12 child_runner.py
```

Replay проверяет сохранённые receipts без model/embedding/rerank calls. Это не
новый live run; сырые generation strings и embedding vectors в старом evidence
не сохранены, поэтому replay ограничен parsed receipts. `FrozenChildMemoryContext`
проверяет source/version/as_of/owner/grant_revision/embedding_generation/digest
на реальном CORE. Мир может смениться на v2 без перезаписи parent v1; revoked
grant, foreign owner, missing source, tamper и generation shift отклоняются.
Невосстановимый snapshot имеет статус unresolved, не «ошибка модели».

## Consumer review реестра v1.3

Ветка `memory/adapter-hardening-20261031`. Точные байты: Attached-Assets
`fd28a9ae57456268ea906f01cbfa0095a4afb14a`. Авторские 31/31 проходят,
раздельные eval/train флаги подтверждены. Consumer review сохраняет блокировку
фактического SFT при `usage=both` и `trainable_base=null`; metadata-only не
открывает evaluation. Это не разрешение на обучение.

`LiveMemoryContext` дополнительно проверяет caller/deadline до retrieval,
grant/deadline/runtime после него, выполняет retrieval в worker thread и ведёт
`active/finished` до фактического завершения потока после отмены. Runtime digest
сверяется через `/api/tags`, а не только записывается в receipt.

```bash
/opt/homebrew/bin/python3.12 training/review_sources.py  # ожидаемый exit 1
```

## G4 consumer: exact MiniMax decision input

Ветка `memory/consumer-g4-20261102` связывает live recall и frozen child v2 с
точным MiniMax envelope `d05e6f969596bcfb0c71427d5c64156a0fb4192f`.
Principal generation берётся из `adapter_contract.request.principal.generation`,
не из CORE SHA. `MAKO_CORE_DIR` и `MAKO_CORE_GIT_HEAD` обязательны; пять
CORE-модулей проверяются по SHA-256 без локального checkout fallback.

```bash
MAKO_CORE_DIR=/path/to/core-2fa \
MAKO_CORE_GIT_HEAD=2fa094082324af15b2c0fa7ad38c95dab92d7147 \
  /opt/homebrew/bin/python3.12 consumer_runner.py
```

Parent v1 остаётся неизменным после v2 и replay не создаёт новый эффект; новая
задача получает v2. Revoked/foreign/missing/tampered/embedding-generation
останавливаются внутри consumer до tool effect. Результат: 16 PASS, 0 FAIL,
2 synthetic effects, 2 result/ACK, внешних/model calls 0. Прежний расход 47/64
сохранён, текущий 0, остаток 17. Это offline adapter на реальном CORE, не live
model run и не независимая приёмка качества. Source closure и hashes:
`integration/consumer-g4-manifest.json`; evidence:
`artifacts/consumer-g4-evidence.json`.

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
