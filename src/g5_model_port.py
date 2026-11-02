#!/usr/bin/env python3
"""core_v1_g5_model_port.py — настоящий model port CORE поверх G5-роутера.

Что это закрывает (волна G4/G5, раздел «mini-max-m5m — настоящий model port
в том же CORE run»): CORE зовёт модель через порт

    await model.generate(model_request) -> ModelResult

где `model_request` — НАСТОЯЩИЙ `ModelRequest`, собранный CORE (в нём messages
с уже извлечённым контекстом памяти, operation_key, principal, deadline,
mode, policy_revision), а ответ — штатный `ModelResult(message, receipt)`.

Этот порт:
  * принимает `ModelRequest` CORE **без перестройки** — messages идут в модель
    как есть, вместе с реальным контекстом памяти;
  * решает маршрут МОИМ reader/selector (core_v1_snapshot_route) по снимку;
  * отправляет вызов исправленным транспортом (core_v1_g5_host_adapter);
  * возвращает штатный CORE-ответ `ModelResult` с полями receipt'а в том же
    составе, что у штатного порта CORE.

Чего порт НЕ делает:
  * не подставляет ответ за модель и не заменяет реальный вызов «похожим»;
  * не объявляет поддержку native tools: путь final-answer не даёт права
    притворяться, что native tools проверены. Запрос с tools → отказ
    CapabilityUnsupported;
  * не продлевает и не подменяет цену: неизвестная цена остаётся неизвестной,
    и тогда работа не начинается.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from core_v1_contract import ModelRequest, ModelResult
from core_v1_errors import (
    CapabilityUnsupported,
    DependencyUnavailable,
    EffectUnknown,
    NoSuitableCandidate,
)
from core_v1_snapshot_route import read_snapshot, select_from_snapshot
from core_v1_task_policy import AdmissionRequest

from core_v1_g5_host_adapter import LanHttpTransport

ROUTE_FIELDS = ("provider", "channel", "model", "version", "region", "mode",
                 "account_type", "currency", "unit")


class G5ModelPort:
    """Model port CORE, решающий маршрут моим reader/selector."""

    def __init__(self, *, snapshot_path: str, base_url: str, route: Mapping[str, Any],
                 admission: Mapping[str, Any] | None = None,
                 price_usd_per_1m: float | None = 0.0,
                 price_source: str = "local-lan-runtime: денежной платы нет "
                                    "(известная нулевая цена)",
                 price_status: str = "known", timeout_s: float = 120.0,
                 policy_descriptor: Mapping[str, Any] | None = None,
                 ledger: Any = None) -> None:
        missing = [name for name in ROUTE_FIELDS if name not in route]
        if missing:
            raise ValueError(
                f"в маршруте нет измерений: {missing} — маршрут объявляет "
                "оператор, он приходит из запроса, а не выдумывается")
        self.snapshot = read_snapshot(snapshot_path)
        self.base_url = base_url.rstrip("/")
        self.route = {name: route.get(name) for name in ROUTE_FIELDS}
        self.admission = AdmissionRequest(**dict(admission or {}))
        self.price_usd_per_1m = price_usd_per_1m
        self.price_source = price_source
        self.price_status = price_status
        self.timeout_s = timeout_s
        self.policy_descriptor = dict(policy_descriptor or {})
        self.ledger = ledger
        self.last_reason = ""

    # ── помощники ──
    def _card(self) -> dict[str, Any]:
        cards = list(getattr(self.snapshot, "cards", []) or [])
        for card in cards:
            if str(card.get("model") or "") == str(self.route.get("model") or ""):
                return dict(card)
        return dict(cards[0]) if cards else {}

    def _offer(self) -> dict[str, Any]:
        card = self._card()
        offer = {name: self.route.get(name) for name in ROUTE_FIELDS}
        offer["id"] = card.get("id") or self.route.get("model") or "candidate"
        offer["card"] = card
        return offer

    @staticmethod
    def _requires_tools(request: ModelRequest) -> bool:
        if request.tools:
            return True
        for message in request.messages or ():
            role = message.get("role") if isinstance(message, Mapping) else getattr(message, "role", None)
            if role in {"tool", "tool_result"}:
                return True
            if isinstance(message, Mapping) and (message.get("tool_calls")
                                                or message.get("tool_call_id")):
                return True
        return False

    # ── порт ──
    async def generate(self, request: ModelRequest) -> ModelResult:
        """Штатная точка входа CORE. Возвращает ModelResult или бросает CoreError."""
        started = time.monotonic()
        if self._requires_tools(request):
            # native tools на этом пути не проверены — честный отказ, а не
            # «chat сработал, значит tools тоже есть».
            # Контракт CORE: у ошибок нет произвольного текста, только
            # машинный код. Причина — в следе порта (self.last_reason).
            self.last_reason = (
                "G5-порт работает по пути final-answer; native tools не "
                "проверены и не заявляются")
            raise CapabilityUnsupported()

        # Версия маршрута обязана совпадать с digest карточки: переименование
        # карточки не даёт права изменить маршрут.
        card = self._card()
        declared = str(self.route.get("version") or "")
        card_digest = str(card.get("model_digest") or "")
        if card_digest and declared and declared != card_digest:
            self.last_reason = (
                f"версия маршрута {declared[:16]}… не совпадает с digest "
                f"карточки {card_digest[:16]}…")
            raise NoSuitableCandidate()
        if not declared and card_digest:
            self.last_reason = (
                "в маршруте не объявлена версия (model_digest карточки) — "
                "маршрут без версии не совпадёт с каноническим ключом")
            raise NoSuitableCandidate()

        now = datetime.now(timezone.utc)
        decision = select_from_snapshot(self.snapshot, [self._offer()],
                                       self.admission, now=now)
        if decision.chosen is None:
            reason = (decision.rejected[0].reason if decision.rejected
                      else "no_candidate")
            self.last_reason = f"маршрут не допущен: {reason}"
            raise NoSuitableCandidate()

        chosen = decision.chosen
        transport = LanHttpTransport(
            base_url=self.base_url, ledger=self.ledger,
            operation_id=request.operation_key,
            attempt_id=f"{request.operation_key}:model",
            run_id=request.run_id, price_usd_per_1m=self.price_usd_per_1m,
            price_source=self.price_source, price_status=self.price_status,
            timeout_s=self.timeout_s)
        outcome = await transport.generate(
            candidate=_Candidate(chosen.provider, chosen.model),
            messages=request.messages,          # ← контекст памяти идёт как есть
            tools=request.tools,
            timeout_s=self.timeout_s, request=request)

        error_code = str(getattr(outcome, "error_code", "") or "")
        if error_code:
            if error_code == "transport_error":
                # запрос ушёл, ответа нет — эффект НЕИЗВЕСТЕН
                self.last_reason = (
                    "вызов отправлен, ответа нет — нужен reconcile, стоимость "
                    "неизвестна (не ноль)")
                raise EffectUnknown()
            self.last_reason = f"канал модели: {error_code}"
            raise DependencyUnavailable()

        elapsed_ms = int((time.monotonic() - started) * 1000)
        receipt = {
            "provider": chosen.provider,
            "model": chosen.model,
            "truncated": False,
            "output_budget": {
                "requested": request.output_limit,
                "model_cap": card.get("context_window"),
                "policy_cap": self.policy_descriptor.get("policy_output_cap"),
                "router_effective": request.output_limit,
            },
            "attempts": [{
                "provider": chosen.provider,
                "model": chosen.model,
                "outcome": "ok",
                "reason": None,
                "tool_calls_stripped": 0,
            }],
            "elapsed_ms": elapsed_ms,
            "usage": dict(getattr(outcome, "usage", {}) or {}),
            "budget_ms": int(self.timeout_s * 1000),
            "mode": request.mode,
            "lease_recovery": None,
            "rejected": [asdict(item) for item in decision.rejected],
            "price_sources": {chosen.model: chosen.price_source},
            "price_statuses": {chosen.model: chosen.price_status},
            "price_routes": {chosen.model: self.route_key()},
            "prices_digest": getattr(self.snapshot, "view_digest", ""),
            "policy_hash": str(self.policy_descriptor.get("policy_hash", "")),
            "policy_role": str(self.policy_descriptor.get("policy_role", "")),
            "policy_task_class": str(
                self.policy_descriptor.get("policy_task_class", "")),
            # что именно ушло в модель — для сверки с памятью
            "g5": {
                "snapshot_sha256": getattr(self.snapshot, "sha256", ""),
                "snapshot_digest": getattr(self.snapshot, "snapshot_digest", ""),
                "card_id": card.get("id"),
                "card_digest": card.get("model_digest"),
                "request_operation_key": request.operation_key,
                "messages": len(list(request.messages or ())),
                "context_digest": self.policy_descriptor.get("context_digest", ""),
            },
        }
        return ModelResult(message=dict(outcome.message or {}), receipt=receipt)

    def route_key(self) -> str:
        from core_v1_applicability import canonical_key, normalize_applicability
        return canonical_key(normalize_applicability(self.route))


class _Candidate:
    """Минимальный кандидат для транспорта (как в порту CORE)."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model_slug = model
        self.supports_tools = False
        self.resource_key = ""
        self.available = True
        self.quality_score = None
        self.price_usd = None
        self.price_valid_until = None
        self.regions = ()
        self.allowed_data_policy = ()
        self.context_window = None
