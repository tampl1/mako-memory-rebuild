"""CLI пилота: манифест закрытия, прогон трёх рук, отчёт (json + markdown)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from memory_pilot import (  # noqa: E402
    ARMS, load_corpus, load_pilot, run_all, score, sha256_file, summarize,
)


def rescore(report_path: Path, pilot: list[dict]) -> dict:
    """Пересчитать проверки готового прогона без повторного вызова модели.

    Правки измерения не должны перезапускать читателя: ответы модели зафиксированы
    в отчёте, меняется только правило проверки.
    """
    report = json.loads(report_path.read_text(encoding="utf-8"))
    by_task = {task["id"]: task for task in pilot}
    for row in report["results"]:
        task = by_task[row["task"]]
        row["checks"] = score(row["answer"], row.get("sources") or [], task["expected"])
    report["summary"] = summarize(report["results"])
    report["manifest"] = {
        "closed_from_candidate": True,
        "rule": "кандидат получает только вопрос и контекст; ожидания лежат в pilot_set.jsonl",
        "pilot_set": {"path": report["manifest"]["pilot_set"]["path"],
                      "sha256": sha256_file(HERE / report["manifest"]["pilot_set"]["path"])},
        "rescored": True,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return report


def build_manifest(corpus_path: Path, pilot_path: Path) -> dict:
    return {
        "closed_from_candidate": True,
        "rule": "кандидат получает только вопрос и контекст; ожидания лежат в pilot_set.jsonl",
        "corpus": {"path": corpus_path.name, "sha256": sha256_file(corpus_path)},
        "pilot_set": {"path": pilot_path.name,
                      "sha256": sha256_file(HERE / pilot_path.name)},
    }


def markdown(report: dict, manifest: dict) -> str:
    lines = []
    lines.append("# Пилот независимой оценки пользы памяти — отчёт прогона")
    lines.append("")
    lines.append("* reader: `%s`" % report["reader"])
    lines.append("* корпус sha256: `%s`" % manifest["corpus"]["sha256"])
    lines.append("* набор sha256: `%s`" % manifest["pilot_set"]["sha256"])
    lines.append("")
    lines.append("| рука | задач | strict OK | ответ | источник | действие | чтение, симв. | запись, симв. | обслуживание, мс | superseded |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for arm, row in report["summary"]["by_arm"].items():
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %d | %s | %d |" % (
            arm, row["tasks"], row["strict_ok"], row["answer_ok"], row["source_ok"],
            row["action_ok"], row["read_chars"], row["write_chars"],
            row["maintenance_ms"], row["superseded"]))
    lines.append("")
    lines.append("## По водяному знаку (эпизодов в архиве)")
    lines.append("")
    lines.append("| рука @ wm | задач | strict OK |")
    lines.append("|---|---|---|")
    for key in sorted(report["summary"]["by_watermark"]):
        row = report["summary"]["by_watermark"][key]
        lines.append("| %s | %d | %d |" % (key, row["tasks"], row["strict_ok"]))
    lines.append("")
    lines.append("## Провалы (публикуем и ухудшения)")
    lines.append("")
    lines.append("| задача | рука | wm | вопрос | ответ | источники | вердикт |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in report["results"]:
        if row["checks"]["strict_ok"]:
            continue
        checks = row["checks"]
        failed = [name for name in ("answer_ok", "source_ok", "action_ok") if not checks[name]]
        lines.append("| %s | %s | %s | %s | %s | %s | не прошло: %s |" % (
            row["task"], row["arm"], row["watermark"], row["question"],
            row["answer"].replace("|", "/"), ", ".join(row["sources"]) or "-",
            ", ".join(failed)))
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reader", default="stub", choices=("stub", "ollama"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--out", default=str(HERE / "pilot_report.json"))
    parser.add_argument("--md", default=str(HERE / "pilot_report.md"))
    parser.add_argument("--corpus", default=str(HERE / "corpus.jsonl"))
    parser.add_argument("--pilot", default=str(HERE / "pilot_set.jsonl"))
    parser.add_argument("--guarded", action="store_true",
                        help="охранительный слой: цитаты-инструкции нейтрализуются "
                             "до чтения, а контекст помечается как данные")
    parser.add_argument("--guard-mode", default="off",
                        choices=("off", "retrieval", "prompt", "both"),
                        help="retrieval — нейтрализация цитат; prompt — конверт в промпте")
    parser.add_argument("--rescore", default="",
                        help="пересчитать проверки готового отчёта, не вызывая модель")
    args = parser.parse_args(argv)

    corpus_path, pilot_path = Path(args.corpus), Path(args.pilot)
    manifest = build_manifest(corpus_path, pilot_path)
    (HERE / "pilot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    pilot = load_pilot(pilot_path)
    if args.rescore:
        report = rescore(Path(args.rescore), pilot)
        report["manifest"] = manifest
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        Path(args.md).write_text(markdown(report, manifest), encoding="utf-8")
        print(markdown(report, manifest))
        return 0

    corpus = load_corpus(corpus_path)
    arms = tuple(x for x in args.arms.split(",") if x)
    results = run_all(corpus, pilot, reader=args.reader, endpoint=args.endpoint,
                      model=args.model, arms=arms, guarded=args.guarded,
                      guard_mode=args.guard_mode)
    report = {
        "reader": args.reader,
        "model": args.model if args.reader == "ollama" else None,
        "guarded": args.guarded,
        "guard_mode": args.guard_mode,
        "manifest": manifest,
        "summary": summarize(results),
        "results": results,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    Path(args.md).write_text(markdown(report, manifest), encoding="utf-8")
    print(markdown(report, manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
