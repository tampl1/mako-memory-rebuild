#!/usr/bin/env python3
"""core_v1_g5_host_adapter.py — минимальный исполняемый G5 host adapter.

Что это: кусок, который replit встраивает в host, чтобы один вызов
CORE-модели прошёл по согласованному маршруту:

    запрос → route_request (мои reader/selector) → CoreModelProvider (существующий
    порт CORE) → настоящий LAN-транспорт → локальный рантайм → ответ →
    канонический учёт → квитанция с ФАКТИЧЕСКИМ результатом.

Ничего нового не изобретается: используются существующие порты
(`core_v1_router_entry.route_request`, `core_v1_model_provider.CoreModelProvider`,
`core_v1_cost_ledger.CostLedger`) и существующий снимок. Второго реестра,
второго SSOT и нового интерфейса не создаётся.

Что этот адаптер НЕ делает:
* не подменяет цену: неизвестная цена остаётся неизвестной (не нулём);
* не обходит роутер: единственный путь к модели — `route_request`;
* не трогает живой воркер памяти, Сферу, чужие порты и очереди.

Пример (встраивание в host):

    adapter = LanHostAdapter(snapshot_path=".../g5-lan-snapshot.json",
                              base_url="http://10.1.0.80:11434",
                              model="qwen2.5:1.5b-instruct",
                              operation_key=f"{run_id}:model:0", run_id=run_id)
    result = adapter.call([{"role": "user", "content": "Say OK."}])
    # result["physical_calls"], result["ledger_records"], result["receipt"]
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from core_v1_cost_ledger import CostLedger, attempt_from_usage
from core_v1_model_provider import ProviderOutcome
from core_v1_router_entry import route_request
from core_v1_snapshot_route import read_snapshot


class LanHttpTransport:
    """Настоящий транспорт по HTTP в локальный (LAN) рантайм модели.

    Пишет в канонический учёт **ровно одну запись на физическую попытку** —
    это и есть источник истины для «был ли вызов». Два дефекта закрыты:

    * **обрыв соединения не теряет учёт.** Прежде исключение уходило наружу
      и попытка исчезала из ledger — «вызова не было» не доказывалось
      ничем. Теперь такая попытка записывается с outcome
      ``timeout_unknown``: запрос мог уйти, стоимость **неизвестна (не ноль)**;
    * **повреждённый JSON больше не выдаётся за успех.** Прежде тело
      разбиралось «мягко», content оставался пустым, а на выход уходил
      успешный outcome — ложный ``ok``. Теперь это ошибка ответа
      (``invalid_response``) и запись в учёт с outcome ``error``.
    """

    def __init__(self, *, base_url: str, ledger: CostLedger | None = None,
                 operation_id: str = "", attempt_id: str = "attempt",
                 run_id: str = "", price_usd_per_1m: float | None = 0.0,
                 price_source: str = "", price_status: str = "known",
                 timeout_s: float = 120.0, record_ledger: bool = True,
                 crash_after_call: bool = False,
                 call_model_override: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.ledger = ledger
        self.operation_id = operation_id
        self.attempt_id = attempt_id
        self.run_id = run_id
        self.price_usd_per_1m = price_usd_per_1m
        self.price_source = price_source
        self.price_status = price_status
        self.timeout_s = timeout_s
        self.record_ledger = record_ledger
        self.crash_after_call = crash_after_call
        self.call_model_override = call_model_override
        self.dispatches: list[dict[str, Any]] = []

    @property
    def dispatch_count(self) -> int:
        return len(self.dispatches)

    @staticmethod
    def runtime_model(candidate) -> str:
        """ПОРТ квалифицирует модель как ``provider:model``; рантайм адресует
        её своим именем. Перевод — работа транспорта, не подмена."""
        slug = str(candidate.model_slug)
        prefix = f"{candidate.provider}:"
        return slug[len(prefix):] if slug.startswith(prefix) else slug

    def _record(self, *, candidate, usage: Mapping[str, Any], outcome: str,
                elapsed_ms: int, detail: str, operation_key: str,
                model: str) -> None:
        """Ровно одна запись на физическую попытку."""
        if self.ledger is None or not self.record_ledger:
            return
        self.ledger.record_attempt(attempt_from_usage(
            operation_id=self.operation_id,
            attempt_id=f"{self.attempt_id}:{len(self.dispatches)}",
            run_id=self.run_id, parent_operation_id=None,
            provider=str(candidate.provider), model=str(candidate.model_slug),
            usage=usage, price_usd_per_1m=self.price_usd_per_1m,
            price_source=self.price_source, price_status=self.price_status,
            elapsed_ms=elapsed_ms, outcome=outcome, detail=detail))

    async def generate(self, *, candidate, messages, tools, timeout_s, request):
        runtime_model = self.call_model_override or self.runtime_model(candidate)
        payload: dict[str, Any] = {
            "model": runtime_model,
            "messages": list(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)
        body = json.dumps(payload).encode("utf-8")
        started = time.monotonic()
        operation_key = str(getattr(request, "operation_key", "") or "")

        def _post() -> tuple[int, bytes, str]:
            http_request = urllib.request.Request(
                self.base_url + "/v1/chat/completions", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(http_request, timeout=timeout_s) as response:
                    return int(response.status), response.read(), ""
            except urllib.error.HTTPError as exc:
                return int(exc.code), exc.read(), ""
            except Exception as exc:                          # noqa: BLE001
                # обрыв: запрос мог уйти — исход НЕИЗВЕСТЕН
                return 0, b"", f"{type(exc).__name__}: {exc}"

        status, raw, transport_error = await asyncio.to_thread(_post)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        usage: dict[str, Any] = {}
        content = ""
        outcome = "ok"
        detail = ""
        error_code = ""

        if transport_error:
            outcome = "timeout_unknown"
            detail = f"обрыв соединения: {transport_error}"
            error_code = "transport_error"
        elif status != 200:
            outcome = "error"
            detail = f"lan http {status}"
            error_code = f"http_{status}"
        else:
            try:
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("ответ не объект JSON")
                usage = dict(data.get("usage") or {})
                choices = data.get("choices") or []
                if choices:
                    content = str((choices[0].get("message") or {})
                                   .get("content") or "")
            except Exception as exc:                          # noqa: BLE001
                # повреждённый ответ — это НЕ успех
                outcome = "error"
                detail = f"нечитаемый ответ: {type(exc).__name__}: {exc}"
                error_code = "invalid_response"
                usage = {}
                content = ""

        self.dispatches.append({
            "model": runtime_model, "model_slug": str(candidate.model_slug),
            "status": status, "elapsed_ms": elapsed_ms,
            "usage": usage, "content": content, "outcome": outcome,
            "error_code": error_code, "operation_key": operation_key,
        })
        self._record(candidate=candidate, usage=usage, outcome=outcome,
                     elapsed_ms=elapsed_ms,
                     detail=detail or f"lan http {status} runtime_model={runtime_model}",
                     operation_key=operation_key, model=runtime_model)
        if self.crash_after_call:
            raise RuntimeError("падение между диспетчем и квитанцией")
        if error_code:
            return ProviderOutcome(error_code=error_code)
        return ProviderOutcome(message={"role": "assistant", "content": content},
                               usage=usage)


class LanHostAdapter:
    """Готовый к встраиванию адаптер: запрос → роутер → LAN → учёт → факт."""

    def __init__(self, *, snapshot_path: str, base_url: str, model: str,
                 operation_key: str, run_id: str,
                 route: Mapping[str, Any],
                 price_usd_per_1m: float | None = 0.0,
                 price_source: str = "local-lan-runtime: денежной платы нет "
                                    "(известная нулевая цена)",
                 price_status: str = "known", timeout_s: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.operation_key = operation_key
        self.run_id = run_id
        self.price_usd_per_1m = price_usd_per_1m
        self.price_source = price_source
        self.price_status = price_status
        self.timeout_s = timeout_s
        self.ledger = CostLedger()
        # Девять измерений маршрута — ОБЯЗАТЕЛЬНЫЙ аргумент конструктора:
        # маршрут берётся из запроса, а не выдумывается из offer'а (в карточке
        # измерений может не быть вовсе). Раньше маршрут можно было не передать,
        # и тогда он молча выводился из карточки — это убрано.
        dimensions = ("provider", "channel", "model", "version", "region",
                      "mode", "account_type", "currency", "unit")
        # Отсутствие ИМЕНИ измерения — ошибка; значение None внутри измерения —
        # законное «unknown» (например, регион неизвестен).
        absent = [name for name in dimensions if name not in route]
        if absent:
            raise ValueError(
                f"в маршруте нет измерений: {absent} — маршрут обязателен "
                "и приходит из запроса, а не из offer'а")
        self.route = {name: route.get(name) for name in dimensions}
        # снимок читаем ПОСЛЕ проверки аргументов: неверный маршрут должен
        # отказывать сразу, не читая файлы
        self.snapshot = read_snapshot(pathlib.Path(snapshot_path))

    def card(self) -> dict[str, Any]:
        cards = list(self.snapshot.cards) if hasattr(self.snapshot, "cards") else []
        for card in cards:
            if str(card.get("model") or "") == self.model:
                return dict(card)
        return dict(cards[0]) if cards else {}

    def request(self, messages: Sequence[Mapping[str, Any]], *,
                tools: Sequence[Mapping[str, Any]] = (),
                admission: Mapping[str, Any] | None = None,
                operation_key: str | None = None,
                route: Mapping[str, Any] | None = None) -> dict[str, Any]:
        card = self.card()
        if route is None:
            route = self.route
        request = {
            **route,
            "id": card.get("id"), "card": card,
            "admission": dict(admission or {"max_cost_usd": 0.0,
                                            "require_price_known": True}),
            "messages": list(messages), "tools": list(tools),
            "run_id": self.run_id,
            "operation_key": operation_key or self.operation_key,
            "deadline_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                         time.gmtime(time.time() + 300)) + "Z",
            "principal": {"owner_id": "host", "tenant_id": "host",
                          "identity_id": "host", "connection_id": "g5",
                          "generation": 1, "key_thumbprint": "host"},
        }
        return request

    def call(self, messages: Sequence[Mapping[str, Any]], *,
             tools: Sequence[Mapping[str, Any]] = (),
             admission: Mapping[str, Any] | None = None,
             operation_key: str | None = None,
             route: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Один вызов по маршруту. Возвращает фактический результат."""
        request = self.request(messages, tools=tools, admission=admission,
                               operation_key=operation_key, route=route)
        transport = LanHttpTransport(
            base_url=self.base_url, ledger=self.ledger,
            operation_id=str(request["operation_key"]),
            attempt_id=str(request["operation_key"]) + ":attempt",
            run_id=self.run_id, price_usd_per_1m=self.price_usd_per_1m,
            price_source=self.price_source, price_status=self.price_status,
            timeout_s=self.timeout_s)
        receipt = route_request(self.snapshot, request, transport=transport)
        chosen = receipt.decision.chosen
        return {
            "operation_key": str(request["operation_key"]),
            "selected_offer": (chosen.candidate_id if chosen else None),
            "selected_card_lineage": (dict(chosen.__dict__)
                                      if chosen is not None and hasattr(chosen, "__dict__")
                                      else None),
            "price_usd": receipt.price_usd,
            "cost_state": receipt.cost_state,
            "price_known": dict(receipt.price_known),
            "price_unknown": list(receipt.price_unknown),
            "dispatch_state": receipt.dispatch_state,
            "refused_before_dispatch": receipt.refused_before_dispatch,
            "effect_unknown": receipt.effect_unknown,
            "reconcile_required": receipt.reconcile_required,
            "reason": receipt.reason,
            "notes": list(receipt.notes),
            "physical_calls": transport.dispatch_count,
            "ledger_records": len(self.ledger.attempts()),
            "ledger": self.ledger.as_dict(),
            "receipt": receipt.as_dict() if hasattr(receipt, "as_dict") else None,
        }
