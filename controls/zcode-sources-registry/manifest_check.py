"""Валидатор реестра источников и моделей (поручение CORE 31.10 п.6, zcode-m5m).

Гейты (replit):
- usage=train требует license_reviewed=true И upstream_code_rights != unknown;
- источники с dangerous_sections — принудительно eval-only;
- датасеты обязаны иметь duplicates_policy и upstream_code_rights не «unknown»;
- модели обязаны иметь digest (sha256, 64 hex);
- execution_rule запрещает исполнение: допустимы no-exec / no-remote-code /
  verified-runtime-only; «exec» в реестре недопустим;
- revision обязателен: git-commit (40 hex) или doc-version.

Выход: список нарушений; exit 1 при нарушениях. Реестр не авторизует
исполнение кода и не меняет discovery/router.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_USAGE = ("metadata-only", "train-possible", "eval-only", "manifest-only")
ALLOWED_EXECUTION = ("no-exec", "no-remote-code", "verified-runtime-only")
USAGES_ALLOWING_TRAIN = ("train-possible", "both")


def _check_source(s: dict) -> list[str]:
    errs: list[str] = []
    sid = s.get("id", "<no-id>")
    p = s.get("pinned") or {}
    rev, rev_kind = p.get("revision"), p.get("revision_kind")
    if p.get("revision_kind") == "document-download":
        # не-git источник: эквивалент закреплённой revision = официальный
        # URL + версия + sha256 полученного документа (не выдуманный git SHA)
        if not (p.get("sha256") and p.get("url") and p.get("version")):
            errs.append(f"{sid}: document-download требует url + version + sha256")
        elif not HEX64.match(str(p.get("sha256"))):
            errs.append(f"{sid}: document-download sha256 не 64-hex")
    else:
        if not rev:
            errs.append(f"{sid}: revision отсутствует")
        elif rev_kind == "git-commit" and not HEX40.match(str(rev)):
            errs.append(f"{sid}: revision не 40-hex git-commit: {rev!r}")
    if not p.get("license_spdx"):
        errs.append(f"{sid}: license_spdx отсутствует")
    usage = s.get("usage")
    if usage not in ALLOWED_USAGE:
        errs.append(f"{sid}: usage {usage!r} вне {ALLOWED_USAGE}")
    if s.get("duplicates_policy") in (None, ""):
        errs.append(f"{sid}: duplicates_policy обязателен")
    exec_rule = s.get("execution_rule")
    if exec_rule not in ALLOWED_EXECUTION:
        errs.append(f"{sid}: execution_rule {exec_rule!r} запрещён — "
                    f"допустимы {ALLOWED_EXECUTION}")
    # ИЕРАРХИЯ USAGE (replit 31.10, разбор поставок): metadata-only <
    # eval-only < train-possible. Unknown license НЕ разрешает evaluation:
    # eval-only требует license_reviewed, иначе только metadata-only (кварантин)
    if usage == "eval-only" and not s.get("license_reviewed"):
        errs.append(f"{sid}: usage=eval-only без license_reviewed — "
                    f"невыясненные права = metadata-only/quarantine")
    # ГЕЙТ ОБУЧЕНИЯ: train требует проверенную лицензию и права upstream
    if usage in USAGES_ALLOWING_TRAIN and not s.get("license_reviewed"):
        errs.append(f"{sid}: usage={usage} без license_reviewed — обучение "
                    f"запрещено до ревью лицензии (лицензия README ≠ права "
                    f"на содержимое)")
    if usage in USAGES_ALLOWING_TRAIN and \
            str(s.get("upstream_code_rights", "")).strip().lower() == "unknown":
        errs.append(f"{sid}: usage={usage} с upstream_code_rights=unknown")
    # ОПАСНЫЕ СЕКЦИИ: train запрещён; eval — только с проверенной лицензией
    if s.get("dangerous_sections") and usage in USAGES_ALLOWING_TRAIN:
        errs.append(f"{sid}: dangerous_sections=true запрещает usage={usage!r}, "
                    f"допустимы eval-only/metadata-only")
    if s.get("kind") == "dataset" and \
            str(s.get("upstream_code_rights", "")).strip().lower() == "unknown":
        errs.append(f"{sid}: dataset с upstream_code_rights=unknown")
    if not s.get("label_source"):
        errs.append(f"{sid}: label_source (источник метки) обязателен")
    if not s.get("coverage"):
        errs.append(f"{sid}: coverage обязателен")
    return errs


def _check_model(m: dict) -> list[str]:
    errs: list[str] = []
    mid = m.get("id", "<no-id>")
    p = m.get("pinned") or {}
    digest = str(p.get("digest") or "")
    if not digest.startswith("sha256:") or not HEX64.match(digest.split(":", 1)[1]):
        errs.append(f"{mid}: digest обязателен (sha256 + 64 hex)")
    if not p.get("license_spdx"):
        errs.append(f"{mid}: license_spdx отсутствует")
    if m.get("usage") in USAGES_ALLOWING_TRAIN and not m.get("license_reviewed"):
        errs.append(f"{mid}: usage с обучением без license_reviewed")
    if m.get("usage") == "eval-only" and not m.get("license_reviewed"):
        errs.append(f"{mid}: usage=eval-only без license_reviewed")
    exec_rule = m.get("execution_rule")
    if exec_rule not in ALLOWED_EXECUTION:
        errs.append(f"{mid}: execution_rule {exec_rule!r} запрещён")
    if m.get("usage") not in ALLOWED_USAGE and m.get("usage") != "both":
        errs.append(f"{mid}: usage {m.get('usage')!r} вне допустимых")
    if not m.get("role"):
        errs.append(f"{mid}: role (назначение) обязательна")
    if m.get("usage") in USAGES_ALLOWING_TRAIN and \
            not m.get("compatibility"):
        errs.append(f"{mid}: LoRA-кандидат обязан зафиксировать совместимость "
                    f"основы/токенизатора (compatibility)")
    return errs


def check_registry(path: str | Path) -> list[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    errs: list[str] = []
    if data.get("schema") != "sources-registry/1.0":
        errs.append("schema != sources-registry/1.0")
    for s in data.get("sources", []):
        errs.extend(_check_source(s))
    for m in data.get("models", []):
        errs.extend(_check_model(m))
    gated = data.get("hf_keys_decision") or {}
    if gated.get("required") and not gated.get("justification"):
        errs.append("hf_keys_decision.required=true без justification — "
                    "обоснование gated-поставки обязательно")
    return errs


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else \
        str(Path(__file__).parent / "sources_registry.json")
    errs = check_registry(path)
    if errs:
        print(f"FAIL: {len(errs)} нарушений реестра {path}:")
        for e in errs:
            print(f"  - {e}")
        return 1
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"PASS: {len(data.get('sources', []))} источников, "
          f"{len(data.get('models', []))} моделей, "
          f"HF-ключи: {'нужны' if (data.get('hf_keys_decision') or {}).get('required') else 'не нужны'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
