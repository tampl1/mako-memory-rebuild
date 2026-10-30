# Архитектура пересборки памяти

## 1. Варианты

### A. Raw-history store + retrieval/reranking

Сохраняет сырые события и подаёт их reader через dense retrieval + cross-encoder/reranking.

- Плюсы: минимальная сложность, низкая ingestion cost, высокая полнота доказательств.
- Минусы: слабые explicit causal/dependency links, стоимость повторного чтения, риск устаревших фактов и длинных контекстов.
- Роль у нас: обязательный сильный baseline и fallback, а не «слабый RAG».

См. Raw-RAG (arXiv:2610.30755): сохранение raw evidence и dependency paths может быть важнее сложных mutations.

### B. Temporal event graph + hybrid projections

Неизменяемые source events порождают temporal claims и доказанные связи. Vector/BM25 находит кандидатов, graph ограничивает/расширяет их, reranker выбирает доказательства.

- Плюсы: corrections, valid time, причинность, actor/recipient, decision→action→result, восстановление as_of.
- Минусы: дороже ingestion, ошибки extraction, необходимы schema discipline и независимые проверки.
- Роль у нас: рекомендуемый основной вариант.

См. Zep (arXiv:2501.13956), AMA-Bench (arXiv:2602.22769), MCCL (arXiv:2610.29357).

### C. Learned/parametric consolidation

Опыт дистиллируется в адаптеры/веса или обучаемые memory policies.

- Плюсы: потенциально быстрый доступ к устойчивым навыкам, procedural transfer.
- Минусы: provenance/deletion/rollback сложнее, риск catastrophic forgetting и закрепления ошибок, зависимость от модели.
- Роль у нас: только поздний исследовательский слой после внешних eval, не canonical память.

См. Sleep (arXiv:2606.03979), MASS и QwenGyre. Семейная история в весах не хранится.

## 2. Рекомендация: B с A как контролем и fallback

Единый модуль состоит из:

1. **Immutable source events** — canonical данные и provenance.
2. **Temporal claims** — утверждения с valid/recorded time, исправлениями и степенью подтверждения.
3. **Evidence links** — только явные ID-связи: reply_to, supersedes, depends_on, derived_from, action_receipt.
4. **Replaceable projections** — vector/BM25, graph, summaries, runbooks; все перестраиваются из источников.
5. **Context Assembler** — owner/project-scoped снимок с бюджетом, причинами выбора и digest.
6. **Durable projection workers** — supervised, idempotent, checkpointed, с health по liveness/backlog.
7. **CORE boundary** — память возвращает доказательства и хранит receipt references; CORE сохраняет execution state и полномочия.

NATS, Redis и Kuzu сохраняются как существующие инфраструктурные возможности, а не обязательные новые уровни. Kuzu — пересобираемая graph projection. NATS — fanout, не canonical outbox. Redis — кэш с owner/model/snapshot в ключе, не источник истины.

## 3. Что сохранить

- Immutable sources и content digests.
- Durable spool/worker retry/recovery и quarantine.
- Owner/project/visibility scopes и negative isolation tests.
- bge-m3/1024 как текущую embedding generation, без смешивания с multilingual-e5.
- Versioned adapter и CORE RunStore/receipt references.
- Raw-history retrieval как production fallback.

## 4. Что написать заново

- Единые IDs и mapping старых IDs.
- SourceEvent / Claim / EvidenceLink / ContextSnapshot / MigrationRecord.
- Инкрементальную invalidation зависимых claims.
- Working snapshots: goal, active entities, open commitments, current evidence.
- Decision→action→result linking и temporal as_of recall.
- Health по живости workers и backlog, а не только HTTP.
- Миграционный ledger со статусом каждого источника.

## 5. Минимальный контракт

### SourceEvent

`event_id`, `source_ref`, `actor`, `recipients`, `owner`, `project`, `session_ref`, `occurred_at`, `ingested_at`, `event_type`, `payload_ref+digest`, `visibility`, `certainty`, `supersedes_event_id`.

### Claim

`claim_id`, `subject_ref`, `predicate`, `object_ref/value`, `evidence_event_ids`, `valid_from/to`, `recorded_at/expired_at`, `status`, `confidence`, `extractor/model_version`, `supersedes_claim_ids`.

### EvidenceLink

`link_id`, `type`, `from_ref`, `to_ref`, `evidence_event_ids`, `certainty=explicit|deterministic|inferred`. Неизвестные связи не создаются.

### ContextSnapshot

`snapshot_id`, `as_of`, `owner`, `project`, `working_goal`, `included_refs`, `excluded_refs+reason`, `budget`, `assembler_version`, `digest`.

### MigrationRecord

`source_system`, `old_id`, `new_event_id`, `status=imported|duplicate|quarantine|error|unavailable`, `reason`, `digest`, `checkpoint`. Молчаливых пропусков нет.

## 6. Критерии пилота

- change fact → invalidate dependent claim → new CORE action;
- старый период доступен по `as_of`;
- restart не повторяет подтверждённый effect;
- замена модели меняет только adapter/receipt, не canonical данные;
- одинаковые входы и бюджеты для no memory / raw+reranking / temporal memory;
- правильное действие и источник важнее красивого текста;
- отрицательные контроли: старый урок, двойной owner, неизвестные автор/дата/связь;
- правило вступает в силу позже: прошлый период не пересчитывается по новой норме;
- поздно полученный документ относится к прежнему периоду;
- injection в документе не становится полномочием;
- накопленная ошибка опровергнута и не отравляет новые решения;
- после роста архива повторяются старые и новые задачи, измеряется интерференция;
- ingestion + retrieval + maintenance учитываются в полной стоимости, а не только reader.

Экспериментальные результаты replit не считаются нашими: no-memory control, повторные проверки после 0/нескольких/многих эпизодов и отрицательный перенос обязательны.
