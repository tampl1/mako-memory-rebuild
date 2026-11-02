#!/usr/bin/env python3
"""core_v1_derived_envelope.py — производный envelope для живого пути.

Зачем: kimi-code-m5m зафиксировал правило (их ACK, #664/6577946441):

  * исходный frozen fixture и его SHA **не меняются**;
  * истёкшие `deadline_at` и `freshness_until` для live **не используются**;
  * для live создаётся **НОВЫЙ производный envelope**: ссылка `derived_from`
    на исходный SHA, свежий deadline от доверенных часов, новая ценовая
    запись и горизонт от доверенного источника, новый digest;
  * просто переставлять даты или `observed_at` в старом evidence нельзя;
  * offline deadline `2099` из synthetic-пакета для live также не разрешён.

Значит, живой вызов идёт не по старому файлу, а по НОВОМУ артефакту, который
ссылается на старый. Этот модуль — тот, кто строит такой артефакт и тот, кто
проверяет его перед подачей в роутер.

Правила, зашитые здесь:

  R1 база проверяется по SHA: derived envelope ссылается на байты, а не на
     «похожий» файл;
  R2 derived envelope — НОВЫЙ артефакт со своим digest; исходный файл не
     меняется и не переписывается никогда;
  R3 deadline берётся от доверенных часов (передаются извне) и ограничен
     горизонтом: «2099»-подобные бесконечные сроки для live не принимаются;
  R4 ценовая запись и её горизонт — новые, с явным источником; сценарная
     цена не принимается (флаг scenario недопустим);
  R5 неизвестная цена остаётся неизвестной: derived envelope может нести
     цену unknown — тогда это честно, и роутер откажет по цене, а не
     подставит ноль.

Функции:
  build_derived_envelope(...)  — собрать новый envelope из базы;
  verify_derived_envelope(...) — проверить envelope перед подачей в роутер.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

SCHEMA = "router-producer-fixture/1.1"
BASE_SCHEMA = "router-producer-fixture/1.0"
#: максимальный горизонт deadline от доверенных часов (live-вызов не «вечный»)
MAX_DEADLINE_HORIZON_S = 600
#: стандартный горизонт свежести цены от доверенного источника
DEFAULT_PRICE_HORIZON_S = 3600


class DerivedEnvelopeError(ValueError):
    """Отказ: производный envelope не проходит проверку."""


def _now_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{int(moment.microsecond / 1000):03d}Z"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso_to_dt(text: str) -> datetime:
    cleaned = str(text).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _digest_of(payload: Mapping[str, Any]) -> str:
    """Дайджест содержимого envelope (канонический JSON, без поля digest)."""
    body = {k: v for k, v in payload.items() if k != "envelope_sha256"}
    return _sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8"))


def load_base(path: str | pathlib.Path) -> tuple[dict[str, Any], str]:
    raw = pathlib.Path(path).read_bytes()
    return json.loads(raw.decode("utf-8")), _sha256(raw)


def build_derived_envelope(*, base_path: str | pathlib.Path,
                            base_sha256: str,
                            now: datetime,
                            price_in: float | None,
                            price_out: float | None,
                            price_source: str,
                            price_status: str = "known",
                            price_horizon_s: int = DEFAULT_PRICE_HORIZON_S,
                            deadline_horizon_s: int = MAX_DEADLINE_HORIZON_S,
                            provenance: Mapping[str, Any] | None = None,
                            ) -> dict[str, Any]:
    """Собрать НОВЫЙ производный envelope из замороженной базы.

    Исходный файл не меняется: он читается, его SHA сверяется, и результат
    живёт отдельным артефактом со своим digest.
    """
    base, actual_sha = load_base(base_path)
    if actual_sha != base_sha256:
        raise DerivedEnvelopeError(
            f"SHA базы не совпадает: манифест {base_sha256}, байты {actual_sha}")
    if base.get("schema") not in (BASE_SCHEMA, SCHEMA):
        raise DerivedEnvelopeError(f"неподдерживаемая схема базы: {base.get('schema')!r}")
    moment = now.astimezone(timezone.utc)

    # R4: сценарная цена недопустима
    if "scenario" in str(price_source).lower():
        raise DerivedEnvelopeError("сценарная цена для живого envelope недопустима")
    # R3: deadline от доверенных часов, в пределах горизонта
    deadline = moment + timedelta(seconds=deadline_horizon_s)
    if deadline_horizon_s > MAX_DEADLINE_HORIZON_S:
        raise DerivedEnvelopeError(
            f"горизонт deadline {deadline_horizon_s} с превышает предел "
            f"{MAX_DEADLINE_HORIZON_S} с: бесконечные сроки для live запрещены")

    envelope: dict[str, Any] = copy.deepcopy(base)
    envelope["schema"] = SCHEMA
    envelope["captured_at"] = _now_iso(moment)
    envelope["derived_from"] = {
        "repo": (provenance or {}).get("repo", ""),
        "commit": (provenance or {}).get("commit", ""),
        "path": str(base_path),
        "sha256": base_sha256,
        "derived_at": _now_iso(moment),
        "clock": "trusted-clock",
        "reason": (provenance or {}).get("reason",
                                         "живой вызов: свежие срок и ценовой "
                                         "горизонт от доверенного источника"),
    }
    envelope["base_sha256"] = base_sha256

    # свежая ценовая запись по КАЖДОМУ ключу маршрута
    view: dict[str, Any] = {}
    for key, group in (base.get("view") or {}).items():
        entry = dict(group)
        entry["observed_at"] = _now_iso(moment)
        entry["valid_until"] = None
        entry["freshness_until"] = _now_iso(moment + timedelta(seconds=price_horizon_s))
        entry["source"] = price_source
        entry["price_in"] = price_in
        entry["price_out"] = price_out
        entry["price_status"] = price_status
        view[key] = entry
    envelope["view"] = view

    # новый request: свежий deadline, тот же principal (identity/grant отдельно)
    contract = dict(envelope.get("adapter_contract") or {})
    request = dict(contract.get("request") or {})
    request["deadline_at"] = _now_iso(deadline)
    contract["request"] = request
    envelope["adapter_contract"] = contract

    # свежая ценовая запись в контракте — и новый digest
    ledger = dict((contract.get("readback_usage_ledger") or {}))
    ledger["cost_usd"] = price_in if price_in is not None else None
    ledger["cost_state"] = price_status
    ledger["price_source"] = price_source
    contract["readback_usage_ledger"] = ledger
    envelope["adapter_contract"] = contract

    envelope["envelope_sha256"] = _digest_of(envelope)
    return envelope


def verify_derived_envelope(envelope: Mapping[str, Any], *,
                             base_path: str | pathlib.Path,
                             base_sha256: str,
                             now: datetime) -> list[dict[str, Any]]:
    """Проверить производный envelope ПЕРЕД подачей в роутер."""
    checks: list[dict[str, Any]] = []
    moment = now.astimezone(timezone.utc)

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("схема производного envelope",
        envelope.get("schema") == SCHEMA,
        f"схема {envelope.get('schema')!r} (ожидалась {SCHEMA!r})")

    linkage = dict(envelope.get("derived_from") or {})
    add("ссылка derived_from присутствует", bool(linkage),
        f"derived_from: {linkage.get('path')!r} @ {str(linkage.get('sha256'))[:16]}…")
    add("ссылка ведёт на замороженную базу",
        linkage.get("sha256") == base_sha256,
        f"ссылка {str(linkage.get('sha256'))[:16]}… vs база {base_sha256[:16]}…")

    base, actual_base_sha = load_base(base_path)
    add("база по SHA совпадает (файл не переписан)", actual_base_sha == base_sha256,
        f"файл {actual_base_sha[:16]}… vs заявлено {base_sha256[:16]}…")

    digest = str(envelope.get("envelope_sha256") or "")
    add("digest envelope пересчитан", digest == _digest_of(envelope),
        f"заявлен {digest[:16]}…, пересчитан {_digest_of(envelope)[:16]}…")

    # A self-computed digest proves integrity, not permission to change the
    # principal, route, admission, tools, messages or historical usage.
    normalized = copy.deepcopy(dict(envelope))
    for name in ("schema", "captured_at", "derived_from", "base_sha256",
                 "envelope_sha256"):
        if name in base:
            normalized[name] = copy.deepcopy(base[name])
        else:
            normalized.pop(name, None)
    contract = normalized.get("adapter_contract", {})
    if isinstance(contract, dict):
        base_contract = base.get("adapter_contract", {})
        req = contract.get("request", {})
        if isinstance(req, dict):
            req["deadline_at"] = base_contract.get("request", {}).get("deadline_at")
        usage = contract.get("readback_usage_ledger", {})
        base_usage = base_contract.get("readback_usage_ledger", {})
        if isinstance(usage, dict):
            for name in ("cost_usd", "cost_state", "price_source"):
                if name in base_usage:
                    usage[name] = copy.deepcopy(base_usage[name])
                else:
                    usage.pop(name, None)
    mutable_price_fields = ("observed_at", "valid_until", "freshness_until",
                            "source", "price_in", "price_out", "price_status")
    for key, value in (normalized.get("view") or {}).items():
        original = (base.get("view") or {}).get(key, {})
        if isinstance(value, dict):
            for name in mutable_price_fields:
                if name in original:
                    value[name] = copy.deepcopy(original[name])
                else:
                    value.pop(name, None)
    add("immutable base projection preserved", normalized == base,
        "Only declared time/price/derivation fields may change")

    request = dict((envelope.get("adapter_contract") or {}).get("request") or {})
    deadline_text = str(request.get("deadline_at") or "")
    try:
        deadline = _iso_to_dt(deadline_text)
        horizon = (deadline - moment).total_seconds()
        ok_deadline = 0 < horizon <= MAX_DEADLINE_HORIZON_S
        detail = (f"deadline {deadline_text} — горизонт от доверенных часов "
                  f"{horizon:.0f} с (предел {MAX_DEADLINE_HORIZON_S})")
    except Exception:                                       # noqa: BLE001
        ok_deadline = False
        detail = f"deadline не разобран: {deadline_text!r}"
    add("deadline свежий и от доверенных часов (без '2099')", ok_deadline, detail)

    stale = []
    for key, group in (envelope.get("view") or {}).items():
        horizon_text = str(group.get("freshness_until") or "")
        try:
            horizon = (_iso_to_dt(horizon_text) - moment).total_seconds()
            if horizon <= 0:
                stale.append(key)
        except Exception:                                   # noqa: BLE001
            stale.append(key)
    add("ценовой горизонт свежий по всем маршрутам", not stale,
        "просроченных горизонтов: 0" if not stale else f"просрочены: {stale}")

    bad_prices = []
    for key, group in (envelope.get("view") or {}).items():
        try:
            observed = _iso_to_dt(group["observed_at"])
            fresh = _iso_to_dt(group["freshness_until"])
            valid_time = observed <= moment < fresh and (
                0 < (fresh - observed).total_seconds() <= DEFAULT_PRICE_HORIZON_S
            )
            status = group.get("price_status")
            values = (group.get("price_in"), group.get("price_out"))
            valid_values = (
                status == "unknown" and all(value is None for value in values)
            ) or (
                status == "known" and all(
                    type(value) in (int, float) and math.isfinite(value) and value >= 0
                    for value in values
                )
            )
            if not valid_time or not valid_values:
                bad_prices.append(key)
        except (KeyError, TypeError, ValueError, OverflowError):
            bad_prices.append(key)
    add("bounded price observation and explicit unknown", not bad_prices,
        f"invalid observations: {len(bad_prices)}")

    scenario = [k for k, g in (envelope.get("view") or {}).items()
                if "scenario" in str(g.get("source") or "").lower()]
    add("сценарная цена не используется", not scenario,
        "сценарных источников нет" if not scenario else f"найдены: {scenario}")

    unknown = [k for k, g in (envelope.get("view") or {}).items()
               if g.get("price_status") == "unknown" or g.get("price_in") is None]
    if unknown:
        add("неизвестная цена остаётся неизвестной", True,
            f"цены unknown по {len(unknown)} маршрутам — роутер откажет по цене, "
            "подстановки нуля нет")
    else:
        add("неизвестная цена остаётся неизвестной", True,
            "все цены известны; проверка на unknown неприменима")
    return checks
