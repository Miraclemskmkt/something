"""已入库但字段不全 — 人工补全队列（勿再跑 OCR 流水线）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from crawler.manual_preserves import is_manual_announcement
from crawler.parser import core_times_complete
from models import Announcement

QUEUE_FILE = Path(__file__).resolve().parent.parent / "data" / "incomplete_manual_queue.json"


def export_incomplete_queue(db: Session) -> list[dict]:
    rows: list[dict] = []
    for ann in db.query(Announcement).order_by(Announcement.id).all():
        if core_times_complete(ann) or is_manual_announcement(ann):
            continue
        missing = []
        if not ann.publish_date:
            missing.append("开放")
        if not ann.deadline:
            missing.append("截止")
        if not ann.event_time:
            missing.append("举办时间")
        if not ann.event_format:
            missing.append("形式")
        rows.append({
            "id": ann.id,
            "university": ann.university,
            "college": ann.college,
            "title": ann.title,
            "url": ann.url,
            "missing": missing,
            "action": "请粘贴通知正文或四字段，提交后标记用户补全保护",
        })
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(
        json.dumps({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(rows),
            "items": rows,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return rows
