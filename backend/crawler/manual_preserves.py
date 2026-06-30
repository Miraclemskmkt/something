"""用户手工补全的通知快照：全量重检时保护四字段不被劣化覆盖或误删。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from crawler.parser import core_times_complete
from models import Announcement

logger = logging.getLogger(__name__)

PRESERVE_FILE = Path(__file__).resolve().parent.parent / "data" / "manual_preserves.json"
MANUAL_SOURCE = "用户补全"

# 会话中用户粘贴全文补全过的学院（按 university+college+college_type）
MANUAL_KEYS: set[tuple[str, str, str]] = {
    ("华东师范大学", "外语学院", "foreign_lang"),
    ("厦门大学", "外文学院", "foreign_lang"),
    ("电子科技大学", "外国语学院", "foreign_lang"),
    ("四川大学", "外国语学院", "foreign_lang"),
    ("西安交通大学", "外国语学院", "foreign_lang"),
    ("兰州大学", "外国语学院", "foreign_lang"),
    ("兰州大学", "法学院", "law"),
    ("天津大学", "法学院", "law"),
    ("上海交通大学", "凯原法学院", "law"),
    ("西安交通大学", "法学院", "law"),
}


def preserve_key(university: str, college: str, college_type: str) -> tuple[str, str, str]:
    return (university, college, college_type)


def is_manual_key(key: tuple[str, str, str]) -> bool:
    return key in MANUAL_KEYS


def is_manual_announcement(ann: Announcement) -> bool:
    key = preserve_key(ann.university, ann.college, ann.college_type)
    if is_manual_key(key):
        return True
    return MANUAL_SOURCE in (ann.source or "")


def _ann_to_dict(ann: Announcement) -> dict:
    return {
        "title": ann.title,
        "url": ann.url,
        "university": ann.university,
        "college": ann.college,
        "college_type": ann.college_type,
        "status": ann.status,
        "event_type": ann.event_type,
        "publish_date": ann.publish_date,
        "deadline": ann.deadline,
        "event_time": ann.event_time,
        "event_format": ann.event_format,
        "source": ann.source or MANUAL_SOURCE,
        "summary": ann.summary,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }


def export_manual_preserves(db) -> int:
    """导出需保护的通知快照到 JSON。"""
    rows: dict[str, dict] = {}

    def score_data(data: dict) -> int:
        s = sum(1 for f in ("publish_date", "deadline", "event_time", "event_format") if data.get(f))
        parts = data.get("university"), data.get("college"), data.get("college_type")
        if len(parts) == 3 and is_manual_key((parts[0], parts[1], parts[2])):
            s += 10
        if MANUAL_SOURCE in (data.get("source") or ""):
            s += 5
        return s

    for ann in db.query(Announcement).all():
        key = preserve_key(ann.university, ann.college, ann.college_type)
        if not is_manual_key(key) and not core_times_complete(ann):
            continue
        k = f"{key[0]}|{key[1]}|{key[2]}"
        cand = _ann_to_dict(ann)
        if k not in rows or score_data(cand) > score_data(rows[k]):
            rows[k] = cand
    PRESERVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESERVE_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已导出 %d 条手工/完整通知快照 → %s", len(rows), PRESERVE_FILE)
    return len(rows)


def load_manual_preserves() -> dict[str, dict]:
    if not PRESERVE_FILE.exists():
        return {}
    try:
        return json.loads(PRESERVE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取快照失败: %s", e)
        return {}


def _item_from_dict(data: dict) -> Announcement:
    return Announcement(
        title=data["title"],
        url=data["url"],
        university=data["university"],
        college=data["college"],
        college_type=data["college_type"],
        status=data.get("status", "active"),
        event_type=data.get("event_type", "夏令营"),
        publish_date=data.get("publish_date"),
        deadline=data.get("deadline"),
        event_time=data.get("event_time"),
        event_format=data.get("event_format"),
        source=data.get("source") or MANUAL_SOURCE,
        summary=data.get("summary"),
    )


def restore_manual_preserves(db) -> tuple[int, int]:
    """重检后恢复缺失或被清空的用户补全数据。返回 (restored, merged)。"""
    from crawler.coverage import mark_coverage
    from crawler.pending import clear_pending_for_announcement

    snapshots = load_manual_preserves()
    if not snapshots:
        return 0, 0

    restored = merged = 0
    for raw_key, data in snapshots.items():
        key = tuple(raw_key.split("|", 2))
        if len(key) != 3:
            continue
        uni, college, ctype = key
        existing = db.query(Announcement).filter(
            Announcement.university == uni,
            Announcement.college == college,
            Announcement.college_type == ctype,
        ).order_by(Announcement.id).first()

        if existing is None:
            ann = _item_from_dict(data)
            db.add(ann)
            db.flush()
            mark_coverage(db, ann)
            clear_pending_for_announcement(db, ann)
            restored += 1
            continue

        changed = False
        snap_url = data.get("url")
        if snap_url and snap_url != existing.url:
            conflict = db.query(Announcement).filter(
                Announcement.url == snap_url,
                Announcement.id != existing.id,
            ).first()
            if not conflict:
                existing.url = snap_url
                changed = True
        for field in ("title", "publish_date", "deadline", "event_time", "event_format", "summary"):
            snap_val = data.get(field)
            cur_val = getattr(existing, field)
            if not snap_val:
                continue
            if is_manual_key(key):
                if cur_val != snap_val:
                    setattr(existing, field, snap_val)
                    changed = True
            elif not cur_val and snap_val:
                setattr(existing, field, snap_val)
                changed = True

        if is_manual_key(key) and MANUAL_SOURCE not in (existing.source or ""):
            existing.source = MANUAL_SOURCE
            changed = True

        if changed:
            existing.updated_at = datetime.now()
            mark_coverage(db, existing)
            clear_pending_for_announcement(db, existing)
            merged += 1

    db.commit()
    logger.info("快照恢复：新增 %d，合并 %d", restored, merged)
    return restored, merged


def prefer_preserve_on_update(existing: Announcement, item) -> None:
    """更新已有记录时：用户补全的四字段不被空值或劣化结果覆盖。"""
    if not is_manual_announcement(existing):
        return
    for field in ("publish_date", "deadline", "event_time", "event_format"):
        old = getattr(existing, field)
        new = getattr(item, field, None)
        if old and not new:
            setattr(item, field, old)
        elif old and new and field in ("deadline", "publish_date"):
            if "23:59:59" in str(new) and "23:59:59" not in str(old) and ":" in str(old):
                setattr(item, field, old)
