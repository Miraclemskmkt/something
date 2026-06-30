"""LLM 补全失败计数与「需人工」标记（JSON 持久化）。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from config import settings

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "llm_enrich_state.json"
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {"records": {}}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"records": {}}


def _save(data: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_record(ann_id: int) -> dict[str, Any]:
    with _lock:
        rec = _load()["records"].get(str(ann_id), {})
    return {
        "fail_count": int(rec.get("fail_count", 0)),
        "needs_manual": bool(rec.get("needs_manual", False)),
        "last_failure": rec.get("last_failure"),
    }


def needs_manual(ann_id: int) -> bool:
    return get_record(ann_id)["needs_manual"]


# 首次失败即标记需人工（重试无意义）
IMMEDIATE_MANUAL_FAILURES = frozenset({"no_content", "pdf_garble", "empty_fields", "forum_needs_link"})


def record_failure(ann_id: int, failure_type: str) -> int:
    with _lock:
        data = _load()
        key = str(ann_id)
        rec = data["records"].setdefault(key, {})
        rec["fail_count"] = int(rec.get("fail_count", 0)) + 1
        rec["last_failure"] = failure_type
        if (
            rec["fail_count"] >= settings.llm_max_failures
            or failure_type in IMMEDIATE_MANUAL_FAILURES
        ):
            rec["needs_manual"] = True
        _save(data)
        return int(rec["fail_count"])


def record_success(ann_id: int) -> None:
    with _lock:
        data = _load()
        key = str(ann_id)
        if key in data["records"]:
            data["records"][key]["fail_count"] = 0
            data["records"][key]["last_failure"] = None
        _save(data)


def clear_manual(ann_id: int) -> None:
    """人工保存后清除需人工标记。"""
    with _lock:
        data = _load()
        key = str(ann_id)
        if key in data["records"]:
            del data["records"][key]
        _save(data)
