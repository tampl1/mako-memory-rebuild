# Memory acceptance adapter

Изолированный callable adapter к общему приёмочному сценарию:
owner/grant → recall → source/version/as_of → frozen child → decision →
result/ACK → restart/replay без нового recall/effect.

Authority provider обязателен. Revoke не восстанавливает grant. Outage,
missing source и generation mismatch выполняются на независимых ready-fixtures;
unrelated `RuntimeError` не считается корректным отказом.

## Команда

```bash
MAKO_CORE_DIR=/path/to/core-4c31/runtime \
MAKO_CORE_GIT_HEAD=4c31e0e0a21a6328e8a8e4e2ebca653e18120f3d \
/opt/homebrew/bin/python3.12 memory_acceptance_adapter.py
```

Evidence: `artifacts/memory-acceptance-evidence.json`.
Source manifest: `integration/memory-acceptance-source-manifest.json`.

Ожидаемый результат: 10/10 checks PASS, result=1, ACK=1, effects=0,
deterministic_model_calls=4, live_model_calls=0, paid_calls=0,
production_writes=0.

Отдельные тесты:

```bash
MAKO_CORE_DIR=/path/to/core-4c31/runtime \
MAKO_CORE_GIT_HEAD=4c31e0e0a21a6328e8a8e4e2ebca653e18120f3d \
/opt/homebrew/bin/python3.12 -m unittest \
  tests.test_memory_acceptance_adapter tests.test_memory_service \
  tests.test_memory_service_manifest -v
```

Это deterministic fixture, не live LLM и не старый memory worker. Старый G4,
GPU, backfill, embeddings и production не используются.
