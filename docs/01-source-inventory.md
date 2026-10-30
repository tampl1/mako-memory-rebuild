# Карта источников и разрывов памяти MAKO

Дата read-only среза: 2026-10-31. Содержимое личных диалогов, credentials и Selectel не использовались. Проверялись только метаданные, агрегаты и схемы.

## 1. Источники

| Группа | Расположение | Формат и объём | IDs / время / авторство | Полнота и разрывы | Статус |
|---|---|---|---|---|---|
| Continuous PostgreSQL | `~/MAKO-Local/continuous-memory/pg17/`, Unix socket, порт 55433 | PG17: 113 sources, 2601 chunks, 1677 embeddings, 78 episodes, 10 facts, 2709 associations | 2026-10-28 00:41:47+03 → 2026-10-31 00:39:23+03; один owner/project/visibility; 5 sessions; user 28 / assistant 85; observation 106, decision 4, outcome 2, correction 1 | 35 pending; 3 spool; embeddings покрывают 64,5% chunks; reply_to отсутствует; локального backup не найдено | Проверено SQL сейчас |
| Текущий continuous-memory код | `…/kimi-code-workdir/mako-continuous-memory/` | Python/JSON/MD; 12 таблиц; bge-m3/1024, 1 generation | external_id, aliases (6), supersedes (1), immutable sources | 38/38 synthetic; production worker остановлен, health не отражает это | Код и evidence проверены |
| Старый стек | `~/MAKO-Local/kimi-code-workdir/mb-20261001/vendor/mako-memory/` | Git mirror, 101 МБ; локальных файлов БД нет | Kuzu/Qdrant/PG были на VM/Selectel; VM-числа июня — только старые отчёты | Локально нет canonical DB; доступные файлы не доказывают текущий объём/целостность | Файлы проверены; VM-числа не подтверждены |
| Attached-Assets | `~/MAKO-Local/Attached-Assets/` | 2,0 ГБ; 558 memory notes; 117 verification docs | source refs, schemas, acceptance evidence | Зеркала/дубли старого кода, не независимый canonical источник | Проверено |
| Kimi Code wire | `~/.kimi-code/sessions/wd_kimi-code-workdir_b77f061c0609/` | 4357 wire.jsonl, 3,85 ГБ, 719470 событий | session/agent/turn/time; append-only; диапазон 2026-09-24 → 2026-10-31 | Только 5 из 4129 session dirs представлены в continuous DB (~0,12%); явного reply_to нет | Проверено по схеме и метаданным, без текста |
| MAKO_Vault | `~/Documents/MAKO_Vault/` | Документы, protocols, architecture, личные диалоги отдельно | source paths/dates; `.git` — symlink в MAKO-Local | Документы не равны фактической БД; личные диалоги не читались | Проверены пути |
| MAKO-Backups | `~/MAKO-Backups/` | Selectel-архивы исключены; остальные evidence dirs | `mako_dev-emergency-20261025.sql.gz` = 20 bytes, stub | Локального backup continuous DB не найдено; Selectel не включался | Проверено |

## 2. Карта восстановления связей

- **Кто и кому:** actor role и owner/project в continuous DB; session/agent/turn в wire. Получатели как отдельные идентификаторы отсутствуют.
- **Сессия/время/проект:** восстанавливается детерминированно по `session_id`, `created_at`, `project` либо session dir и wire `time`.
- **Reply:** явного `reply_to` нет. Порядок `turn_index`/`turnId` доказывает последовательность, но не факт ответа.
- **Решение → действие → результат:** `event_type`, `supersedes_external_id`, `facts.superseded_by` дают начало модели, но выборка мала. Wire содержит события выполнения и receipts, однако связь с continuous sources покрыта не полностью.
- **Объединение поколений:** допускается только по стабильным IDs и digest/mapping. Текстовое сходство не является доказательством личности, дубликата или причинной связи.

## 3. Главные разрывы

1. Два поколения памяти имеют разные IDs, embedding generations и графы.
2. Новый continuous recall не подключён к старому LangGraph `gather_context`.
3. Production worker остановлен; health не учитывает worker liveness/backlog.
4. Большая часть wire-истории не представлена в continuous DB.
5. Episodes являются обрезанным текстом; claims/procedural memory почти отсутствуют.
6. Kuzu и PostgreSQL associations — независимые графы, а не одна проекция.
7. Нет локального backup continuous DB.
8. Документ replit `memory-experimental-evidence-20261030` сначала не был найден в деревьях; затем получен полным текстом через ручную передачу владельца и учтён как экспериментальное основание, не как отчёт о нашем воспроизведении.

## 4. Границы доказательств

- **Проверено сейчас:** агрегаты live PG, файловые метаданные, схемы wire, пути и объёмы.
- **Старые отчёты:** VM-side Chronicle/Episodes/Facts/Kuzu/Qdrant — не перепроверено.
- **Недоступно:** Selectel, VM volumes и содержимое личных диалогов. Replit evidence получен вручную, но его экспериментальные результаты не являются нашими.
