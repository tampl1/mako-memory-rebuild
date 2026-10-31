# Sleep framework: feasibility for MAKO

Статус: feasibility note по [Google Sleep](https://research.google/blog/the-sleep-framework-a-new-way-for-llms-to-retain-knowledge-in-continual-learning/), не запуск обучения и не новая policy.

## Что требует исходной архитектуры

- Mixture-of-Experts / Hope с заранее выделенными блоками памяти.
- Разблокировка новых low-rank experts во время offline sleep.
- Два уровня параметрической памяти: временная высокочастотная и долговременная низкочастотная.
- Consolidation через generalized knowledge distillation и on-policy imitation.
- Dreaming: random expert activation, генерация/фильтрация примеров и RL refinement.
- Сброс временных параметров после консолидации.

Простой dense LoRA не воспроизводит эту архитектуру. Добавление адаптера не доказывает отсутствие catastrophic forgetting и не является репликацией Sleep.

## Что можно воспроизвести с LoRA/replay

- Чередование wake/offline фаз без изменения production-весов.
- Отдельный versioned adapter на проверенном накопленном опыте.
- Rehearsal старых примеров рядом с новыми для снижения forgetting.
- Frozen baseline и candidate, heldout, retention, false-refusal и leakage controls.
- Shadow evaluation после каждого цикла и rollback к предыдущему adapter.
- Внешняя проверяемая память фактов вместо переноса всей истории в веса.

Это lifecycle «по мотивам», не реализация статьи.

## Что нельзя называть репликацией

- Ночной SFT на свежих диалогах.
- Автоматическое дообучение штатной модели после каждого задания.
- Self-generated dreams без проверяемого отбора и независимой оценки.
- Заявление о long-term memory по одному улучшению на маленьком holdout.

## Практический вариант для 14B

1. Сначала LoRA + replay, не MoE/Hope.
2. Только reviewed data с lineage и правами; holdout не входит в train.
3. Независимые baseline/after и retention до установки адаптера.
4. Ночная сборка кандидата — отдельный run с lease, бюджетом и journal.
5. Если нужна именно статья — отдельный research track с MoE/Hope, capacity expansion и доказательством сброса временной памяти.

Память и веса остаются разными: история переносится между моделями, adapter — версионированная специализация.
