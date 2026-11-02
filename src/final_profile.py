"""M1M producer profile to executable G5 model-port snapshot."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from core_v1_cost_ledger import CostLedger
from core_v1_snapshot_digest import digest_catalog, digest_snapshot, digest_view
from .g5_model_port import G5ModelPort


def load_profile(path):
    profile = json.loads(Path(path).read_text())
    if profile.get("schema") != "g4-final-m1m-profile/1.0":
        raise ValueError("unexpected M1M producer profile")
    if profile.get("binding_generation") != 2 or profile["card"].get("node") != "m1m":
        raise ValueError("M1M binding generation/node mismatch")
    if profile["card"].get("endpoint") != "http://10.1.0.75:11434/v1":
        raise ValueError("physical M1M endpoint mismatch")
    if profile["card"].get("model_digest") != profile["route"].get("version"):
        raise ValueError("route version differs from model card digest")
    return profile


def build_snapshot(profile, price, workdir):
    cards = [copy.deepcopy(profile["card"])]
    for card in cards:
        card["last_seen_at"] = price["observed_at"]
        card["freshness_s"] = 0
    view = copy.deepcopy(profile["view"])
    for entry in view.values():
        entry["observed_at"] = price["observed_at"]
        entry["freshness_until"] = price["freshness_until"]
        entry["price_in"] = price["price_in"]
        entry["price_out"] = price["price_out"]
        entry["source"] = price["source"]
        entry["price_status"] = price["price_status"]
    payload = {
        "schema": "catalog-snapshot/1.0",
        "captured_at": price["observed_at"],
        "sources": {"producer": profile["source"], "canonicalization_spec_version": "1.0"},
        "freshness_policy": {"stale_ttl_s": 1800, "fresh_max_s": 600, "frozen": True},
        "rules": {"role": "receipt-input-not-ssot", "grants_rights": False},
        "counts": {"cards": 1, "unknown_digest": 0, "digest_changed": 0, "smoke_current": 0},
        "cards": cards, "view": view, "view_digest": digest_view(view),
    }
    payload["catalog_digest"] = digest_catalog(cards)
    payload["snapshot_digest"] = digest_snapshot(payload)
    path = Path(workdir) / "m1m-snapshot.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    return path, payload


def make_port(profile, snapshot_path, price, *, context_digest, ledger=None):
    return G5ModelPort(
        snapshot_path=str(snapshot_path), base_url=profile["endpoint"],
        route=profile["route"], admission={"max_cost_usd": 0.0, "require_price_known": True},
        price_usd_per_1m=price["price_out"], price_source=price["source"],
        price_status=price["price_status"], timeout_s=120,
        policy_descriptor={"context_digest": context_digest,
                           "policy_role": "g4-final-answer",
                           "policy_task_class": "memory-consumer"},
        ledger=ledger if ledger is not None else CostLedger(),
    )
