"""从论坛帖子发现的学院官网通知链接（运行时合并进爬取注册表）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DISCOVERY_FILE = Path(__file__).resolve().parent.parent / "data" / "discovered_college_urls.json"


def _load() -> dict:
    if not DISCOVERY_FILE.is_file():
        return {"entries": []}
    try:
        return json.loads(DISCOVERY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": []}


def _save(data: dict) -> None:
    DISCOVERY_FILE.parent.mkdir(parents=True, exist_ok=True)
    DISCOVERY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def discovered_urls_map() -> dict[tuple[str, str, str], list[str]]:
    """(university, college, college_type) -> [url, ...]"""
    out: dict[tuple[str, str, str], list[str]] = {}
    for e in _load().get("entries", []):
        key = (e.get("university", ""), e.get("college", ""), e.get("college_type", ""))
        url = (e.get("url") or "").strip()
        if not all(key) or not url:
            continue
        out.setdefault(key, [])
        if url not in out[key]:
            out[key].append(url)
    return out


def record_discovered_url(
    *,
    university: str,
    college: str,
    college_type: str,
    url: str,
    source: str = "baoyan_forum",
) -> bool:
    """记录论坛发现的官方通知链接。返回是否为新 URL。"""
    url = (url or "").strip()
    if not url or "edu.cn" not in url and "ac.cn" not in url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False

    data = _load()
    entries: list = data.setdefault("entries", [])
    for e in entries:
        if (
            e.get("university") == university
            and e.get("college") == college
            and e.get("college_type") == college_type
            and e.get("url") == url
        ):
            return False

    entries.append({
        "university": university,
        "college": college,
        "college_type": college_type,
        "url": url,
        "domain": parsed.netloc,
        "source": source,
        "discovered_at": datetime.now().isoformat(timespec="seconds"),
    })
    entries[:] = entries[-500:]
    _save(data)
    logger.info("发现学院通知链接 %s %s: %s", university, college, url[:80])
    return True
