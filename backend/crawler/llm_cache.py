"""LLM 提取结果缓存 + 人工校正 few-shot 示例库。"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = _DATA / "llm_extract_cache.json"
FEW_SHOT_FILE = _DATA / "llm_few_shots.json"
MAX_FEW_SHOTS = 8
MAX_CACHE_ENTRIES = 500


def _load_json(path: Path) -> dict | list:
    if not path.is_file():
        return {} if path == CACHE_FILE else []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("load %s failed: %s", path.name, e)
        return {} if path == CACHE_FILE else []


def _save_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_key(url: str, title: str) -> str:
    raw = f"{url}|{(title or '')[:80]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def get_cached_extract(url: str, title: str) -> dict | None:
    store = _load_json(CACHE_FILE)
    if not isinstance(store, dict):
        return None
    entry = store.get(cache_key(url, title))
    if entry and entry.get("fields"):
        return entry
    return None


def set_cached_extract(
    url: str,
    title: str,
    fields: dict,
    *,
    confidence: float,
    source: str = "llm",
) -> None:
    store = _load_json(CACHE_FILE)
    if not isinstance(store, dict):
        store = {}
    key = cache_key(url, title)
    store[key] = {
        "url": url,
        "title": (title or "")[:200],
        "fields": fields,
        "confidence": confidence,
        "source": source,
        "cached_at": datetime.now().isoformat(timespec="seconds"),
    }
    if len(store) > MAX_CACHE_ENTRIES:
        sorted_keys = sorted(
            store.keys(),
            key=lambda k: store[k].get("cached_at", ""),
        )
        for k in sorted_keys[: len(store) - MAX_CACHE_ENTRIES]:
            store.pop(k, None)
    _save_json(CACHE_FILE, store)


def load_few_shots() -> list[dict]:
    data = _load_json(FEW_SHOT_FILE)
    return data if isinstance(data, list) else []


def format_few_shots_for_prompt() -> str:
    shots = load_few_shots()
    if not shots:
        return _default_few_shots_text()
    parts: list[str] = []
    for i, ex in enumerate(shots[:MAX_FEW_SHOTS], 1):
        parts.append(
            f"示例{i}:\n标题:{ex.get('title', '')}\n"
            f"正文片段:{(ex.get('content_snippet') or '')[:400]}\n"
            f"输出:{json.dumps(ex.get('fields', {}), ensure_ascii=False)}"
        )
    return "\n\n".join(parts)


def add_few_shot_from_manual(
    *,
    title: str,
    content_snippet: str,
    fields: dict,
    university: str = "",
    college: str = "",
) -> None:
    """人工校正后写入 few-shot，供后续 prompt 自动引用。"""
    shots = load_few_shots()
    entry = {
        "title": title,
        "content_snippet": (content_snippet or "")[:600],
        "fields": {k: v for k, v in fields.items() if v},
        "university": university,
        "college": college,
        "added_at": datetime.now().isoformat(timespec="seconds"),
    }
    shots = [s for s in shots if s.get("title") != title]
    shots.insert(0, entry)
    shots = shots[:MAX_FEW_SHOTS]
    _save_json(FEW_SHOT_FILE, shots)
    logger.info("few-shot 已更新: %s", (title or "")[:40])


def compute_field_confidence(fields: dict[str, str | None]) -> float:
    keys = ("publish_date", "deadline", "event_time", "event_format")
    filled = sum(1 for k in keys if fields.get(k))
    return filled / len(keys)


def _default_few_shots_text() -> str:
    return """示例1（线上）:
标题:关于外语学院举办2026年优秀大学生夏令营活动的通知
正文:报名起止时间2026年7月1日9:00至7月2日12:00，7月7日线上远程举办。
输出:{"publish_date":"2026-07-01 09:00:00","deadline":"2026-07-02 12:00:00","event_start":"2026-07-07","event_end":"2026-07-07","event_format":"线上"}

示例2（线下）:
标题:法学院2026年全国优秀大学生夏令营通知
正文:网上报名6月20日至6月25日17:00，夏令营7月10日-12日在学院线下举行。
输出:{"publish_date":"2026-06-20 00:00:00","deadline":"2026-06-25 17:00:00","event_start":"2026-07-10","event_end":"2026-07-12","event_format":"线下"}

示例3（时间模糊）:
标题:外国语学院2026年推免夏令营招生简章
正文:报名截止7月15日，夏令营7月下旬举办，形式待定。
输出:{"publish_date":null,"deadline":"2026-07-15 23:59:59","event_start":"2026-07-20","event_end":null,"event_format":null}"""
