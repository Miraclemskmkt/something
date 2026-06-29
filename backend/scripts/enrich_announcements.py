"""从通知原文页补全开放填报、截止填报、举办时间与举办形式（历史数据回填）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from config import settings
from crawler.detail_enricher import enrich_announcement, infer_board_from_item
from crawler.listing_resolver import is_listing_url
from crawler.parser import ParsedAnnouncement, extract_date_from_url
from database import SessionLocal, init_db
from models import Announcement


def _is_garbage_event(val: str | None) -> bool:
    if not val:
        return True
    bad = ("发布", "点击", "全程", "十年", "全国", "活动", "指南", "入选", "法学", "学院")
    if any(b in val for b in bad) and not __import__("re").search(
        r"\d{1,2}[月\-/.]|至|到|\d{4}年|\d{4}-\d{2}-\d{2}", val
    ):
        return True
    return len(val) > 60 and not __import__("re").search(r"\d{1,2}[月\-/.]|至|到|\d{4}", val)


async def enrich_one(
    ann: Announcement,
    client: httpx.AsyncClient,
    db,
    *,
    force: bool = True,
) -> bool:
    item = ParsedAnnouncement(
        title=ann.title,
        url=ann.url,
        publish_date=ann.publish_date,
        deadline=ann.deadline,
        event_time=ann.event_time,
        event_format=ann.event_format,
        university=ann.university,
        college=ann.college,
        college_type=ann.college_type,
    )
    board = infer_board_from_item(item)
    changed = False
    await enrich_announcement(item, board=board, phase="notice", client=client)

    if is_listing_url(item.url) and not item.deadline:
        from crawler.url_quality import assess_notice_url
        level, reason = assess_notice_url(item)
        note = f"汇总/可疑页面({reason or '无截止'})，已尝试重新定位"
        if ann.summary != note:
            ann.summary = note

    if not item.deadline:
        from crawler.detail_enricher import resolve_better_url
        from crawler.url_quality import assess_notice_url
        if await resolve_better_url(item, board=board, phase="notice", client=client):
            changed = True
    if item.url and item.url != ann.url:
        conflict = db.query(Announcement).filter(
            Announcement.url == item.url, Announcement.id != ann.id,
        ).first()
        if conflict:
            db.delete(conflict)
            db.flush()
        ann.url = item.url
        changed = True
    if item.title and item.title != ann.title and len(item.title) >= len(ann.title or ""):
        ann.title = item.title
        changed = True
    if item.summary and item.summary != ann.summary:
        ann.summary = item.summary
        changed = True
    for field in ("publish_date", "deadline", "event_time", "event_format"):
        new_val = getattr(item, field)
        old_val = getattr(ann, field)
        if field == "event_time" and _is_garbage_event(new_val):
            new_val = None
        if new_val and (force or not old_val) and old_val != new_val:
            setattr(ann, field, new_val)
            changed = True
        elif force and field == "event_time" and not new_val and old_val and _is_garbage_event(old_val):
            setattr(ann, field, None)
            changed = True

    if not ann.publish_date and ann.url:
        pub = extract_date_from_url(ann.url)
        if pub:
            ann.publish_date = pub
            changed = True

    return changed


async def main() -> None:
    init_db()
    db = SessionLocal()
    headers = {"User-Agent": settings.user_agent}
    try:
        rows = db.query(Announcement).order_by(Announcement.university).all()
        updated = 0
        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers=headers,
            verify=False,
        ) as client:
            for ann in rows:
                if await enrich_one(ann, client, db, force=True):
                    updated += 1
                flag = "OK" if ann.deadline or ann.event_time else "--"
                if is_listing_url(ann.url):
                    flag = "LIST"
                print(
                    f"{flag} {ann.university} {ann.college}\n"
                    f"    url={ann.url[:70]}\n"
                    f"    open={ann.publish_date or '-'}\n"
                    f"    deadline={ann.deadline or '-'}\n"
                    f"    event={ann.event_time or '-'}  format={ann.event_format or '-'}"
                )
        db.commit()
        filled = sum(1 for a in rows if a.deadline)
        print(f"\n完成：共 {len(rows)} 条，更新 {updated} 条，已有截止填报 {filled} 条")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
