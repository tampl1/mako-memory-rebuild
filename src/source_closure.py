"""Required, hash-pinned CORE source closure; no checkout fallback."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

CORE_COMMIT = "2fa094082324af15b2c0fa7ad38c95dab92d7147"
DERIVED_CORE_COMMIT = "4c31e0e0a21a6328e8a8e4e2ebca653e18120f3d"
CORE_PATH = "scripts/backend/mnemosyne-core"
CORE_MODULES = {
    "core_v1_contract.py": "a43e1e111a5b23892c44d24938bbd97af4a922793c773bfb2838aca56c315b30",
    "core_v1_errors.py": "a9b76b97a756b00af3f27e66bf1b5d669eaf9ec31d7976e96cd7b844dd62c09b",
    "core_v1_output_budget.py": "6bb3dd15ccfb534bd60df95928196f51346a17d2b16ca54f0c3298ae5ed8b303",
    "core_v1_runtime.py": "f13ffec9e9f81c77588355fb1aaf88affb8727d5bb9c26c9ed19e1bc502c4d64",
    "core_v1_store.py": "3cd5780bda29e13f40a71a6d8f2d5925dd94a54316ab0b23e7c246fa12fb56e3",
}
DERIVED_CORE_MODULES = {
    "core_v1_contract.py": "a43e1e111a5b23892c44d24938bbd97af4a922793c773bfb2838aca56c315b30",
    "core_v1_errors.py": "201f01f14188efdc837813851be4a566276a0c425e0819db338627c35bb23479",
    "core_v1_output_budget.py": "6bb3dd15ccfb534bd60df95928196f51346a17d2b16ca54f0c3298ae5ed8b303",
    "core_v1_runtime.py": "f13ffec9e9f81c77588355fb1aaf88affb8727d5bb9c26c9ed19e1bc502c4d64",
    "core_v1_store.py": "cfb54a754ee455b92f924e9e399c5da565659338bba930a0f0698492e1a18167",
}
CORE_PROFILES = {CORE_COMMIT: CORE_MODULES, DERIVED_CORE_COMMIT: DERIVED_CORE_MODULES}
FULL_INVENTORY = Path(__file__).resolve().parent.parent / "integration/core-4c31-runtime.sha256.json"
FULL_INVENTORY_SHA256 = "6ec41dff8dfe906265931b58fb1079651438610b40f313e23ada68b4c00db659"


class SourceClosureError(Exception):
    pass


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configure_core():
    core_dir = os.environ.get("MAKO_CORE_DIR")
    core_head = os.environ.get("MAKO_CORE_GIT_HEAD")
    if not core_dir or not core_head:
        raise SourceClosureError("MAKO_CORE_DIR and MAKO_CORE_GIT_HEAD are required")
    if core_head not in CORE_PROFILES:
        raise SourceClosureError("CORE commit does not match a frozen consumer profile")
    root = Path(core_dir).resolve()
    if not root.is_dir():
        raise SourceClosureError("MAKO_CORE_DIR is not a directory")
    measured = {}
    modules = CORE_PROFILES[core_head]
    if core_head == DERIVED_CORE_COMMIT:
        if sha256_file(FULL_INVENTORY) != FULL_INVENTORY_SHA256:
            raise SourceClosureError("full CORE inventory digest mismatch")
        modules = json.loads(FULL_INVENTORY.read_text())
    for name, expected in modules.items():
        path = root / name
        if not path.is_file():
            raise SourceClosureError(f"missing CORE runtime module: {name}")
        actual = sha256_file(path)
        if actual != expected:
            raise SourceClosureError(f"CORE module digest mismatch: {name}")
        measured[name] = actual
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return {"commit": core_head, "path": CORE_PATH, "modules": measured}
