# Оценка ресурсов

## 1. Измерено независимо на пилоте

Команда: `/usr/bin/time -l /opt/homebrew/bin/python3.12 runner.py`.

- Длительность runner: **0,29–0,45 s** между режимами измерения;
- `ru_maxrss`: **32 587 776 bytes (~31,1 MiB)**;
- 14 unittest: **0,538 s**;
- платные вызовы: 0;
- сетевые/фоновые процессы после проверки: 0;
- хранилище: временный SQLite в `TemporaryDirectory`, удалён.

Это стоимость маленького synthetic pilot, а не прогноз полного backfill.

## 2. Что нельзя честно утверждать сейчас

- Время полного переноса 3,85 ГБ wire и старых БД;
- RAM полного embeddings/graph pipeline;
- Качество/стоимость живых LLM extraction и reranking;
- Production latency после включения нового контура.

Эти величины требуют ограниченного реального образца и не экстраполируются из 14 unit tests.

## 3. Формула оценки полного переноса

Для каждого источника отдельно учитывать:

`T_total = T_read + T_validate + T_embed + T_link + T_projection + T_verify + T_final_delta`

`C_total = C_compute + C_reader + C_embedding + C_rerank + C_storage + C_maintenance`

Обязательные измерения на образце:

- events/s и bytes/s по каждому источнику;
- peak RSS worker и reader;
- SQLite/PG/Kuzu/vector storage после проекций;
- retries, quarantine и error rate;
- ingestion p50/p95/max и recall p50/p95;
- число model calls на событие и на запрос;
- стоимость повторного восстановления после crash.

## 4. Ограничения полного переноса

- Обработка допускает дни, но разбивается на checkpoints с возобновлением.
- Массовые платные вызовы не запускаются без отдельного бюджета.
- Дедупликация обязана до embeddings, чтобы не платить повторно.
- Raw-RAG baseline не требует transformation cost; temporal graph сравнивается с ним по полной цене, а не только по accuracy.
- Рост архива проверяется на старых и новых задачах, чтобы измерить интерференцию и отрицательный перенос.
