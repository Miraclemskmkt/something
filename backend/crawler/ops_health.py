"""运维健康度：人工积压、论坛爬虫连续空跑等。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from crawler.field_enricher import all_fields_complete, summary_has_extended
from crawler.llm_enrich_state import needs_manual
from models import Announcement

_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "forum_crawl_state.json"
NEEDS_MANUAL_WARN = 10
FORUM_EMPTY_STREAK_WARN = 2


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {"runs": []}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"runs": []}


def _save_state(data: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_forum_crawl(*, found: int, new: int) -> None:
    data = _load_state()
    runs: list = data.setdefault("runs", [])
    runs.append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "found": found,
        "new": new,
    })
    data["runs"] = runs[-20:]
    _save_state(data)


def forum_empty_streak() -> int:
    runs = _load_state().get("runs", [])
    streak = 0
    for r in reversed(runs):
        if int(r.get("found", 0)) == 0 and int(r.get("new", 0)) == 0:
            streak += 1
        else:
            break
    return streak


def count_needs_manual(db: Session) -> int:
    return sum(1 for a in db.query(Announcement).all() if needs_manual(a.id))


def count_missing_extended(db: Session) -> int:
    return sum(
        1 for a in db.query(Announcement).all()
        if all_fields_complete(a) and not summary_has_extended(a.summary)
    )


def list_missing_extended(db: Session, limit: int = 30) -> list[dict]:
    rows = [
        a for a in db.query(Announcement).order_by(Announcement.id).all()
        if all_fields_complete(a) and not summary_has_extended(a.summary)
    ]
    out: list[dict] = []
    for a in rows[:limit]:
        out.append({
            "id": a.id,
            "university": a.university,
            "college": a.college,
            "title": a.title,
            "url": a.url,
            "source": a.source or "",
            "summary_len": len((a.summary or "").split("---扩展信息---")[0].strip()),
        })
    return out


def build_ops_health(db: Session) -> dict:
    manual = count_needs_manual(db)
    missing_ext = count_missing_extended(db)
    streak = forum_empty_streak()
    runs = _load_state().get("runs", [])
    last = runs[-1] if runs else None
    alerts: list[dict] = []
    if manual >= NEEDS_MANUAL_WARN:
        alerts.append({
            "level": "warn",
            "code": "needs_manual_high",
            "message": f"需人工补全已累积 {manual} 条，请优先处理字段补全 Tab",
        })
    if streak >= FORUM_EMPTY_STREAK_WARN:
        alerts.append({
            "level": "warn",
            "code": "forum_crawl_empty",
            "message": f"论坛爬虫连续 {streak} 轮零发现，请检查版块配置或网站结构",
        })
    return {
        "needs_manual": manual,
        "missing_extended": missing_ext,
        "forum_empty_streak": streak,
        "forum_last_run": last,
        "alerts": alerts,
    }
