"""检索/列表页 TTL 缓存（内存 + 可选持久化种子 URL）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

SEEDS_FILE = Path(__file__).resolve().parent.parent / "data" / "college_list_seeds.json"

_mem: dict[str, tuple[float, object]] = {}


def _get(key: str) -> object | None:
    row = _mem.get(key)
    if not row:
        return None
    exp, val = row
    if time.time() > exp:
        _mem.pop(key, None)
        return None
    return val


def _set(key: str, val: object, ttl_sec: int) -> None:
    _mem[key] = (time.time() + ttl_sec, val)


def get_search_cache(query: str) -> list | None:
    val = _get(f"search:{query}")
    return val if isinstance(val, list) else None


def set_search_cache(query: str, results: list, ttl_sec: int) -> None:
    _set(f"search:{query}", results, ttl_sec)


def get_list_page(url: str) -> str | None:
    val = _get(f"list:{url}")
    return val if isinstance(val, str) else None


def set_list_page(url: str, html: str, ttl_sec: int) -> None:
    if html and len(html) >= 200:
        _set(f"list:{url}", html, ttl_sec)


def _load_seeds() -> dict[str, list[str]]:
    if not SEEDS_FILE.exists():
        return {}
    try:
        return json.loads(SEEDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_seeds(data: dict[str, list[str]]) -> None:
    SEEDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def seed_key(university: str, college: str, college_type: str) -> str:
    return f"{university}|{college}|{college_type}"


def get_list_seeds(university: str, college: str, college_type: str) -> list[str]:
    return list(_load_seeds().get(seed_key(university, college, college_type), []))


def save_list_seeds(university: str, college: str, college_type: str, urls: list[str]) -> None:
    if not urls:
        return
    data = _load_seeds()
    key = seed_key(university, college, college_type)
    merged = list(dict.fromkeys((data.get(key) or []) + urls))[:5]
    data[key] = merged
    _save_seeds(data)
