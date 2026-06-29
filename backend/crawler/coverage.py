"""学院检索覆盖状态：已找到的通知类型不再重复爬取。"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from config import settings
from crawler.boards import (
    BOARD_SLOTS,
    PRE_ADMISSION,
    SUMMER_CAMP,
    classify_slot,
)
from models import Announcement, CrawlCoverage

logger = logging.getLogger(__name__)


def _slot_for_phase(board: str, phase: str) -> str:
    from crawler.boards import (
        SLOT_PRE_ADMISSION_NOTICE,
        SLOT_PRE_ADMISSION_RESULT,
        SLOT_SUMMER_CAMP_NOTICE,
        SLOT_SUMMER_CAMP_RESULT,
    )

    return {
        (SUMMER_CAMP, "notice"): SLOT_SUMMER_CAMP_NOTICE,
        (SUMMER_CAMP, "result"): SLOT_SUMMER_CAMP_RESULT,
        (PRE_ADMISSION, "notice"): SLOT_PRE_ADMISSION_NOTICE,
        (PRE_ADMISSION, "result"): SLOT_PRE_ADMISSION_RESULT,
    }[(board, phase)]


def colleges_with_official_slot(db: Session, board: str, phase: str) -> set[tuple[str, str, str]]:
    """数据库中已有学院官网来源、且对应当前检索槽位的学院。"""
    slot = _slot_for_phase(board, phase)
    keys: set[tuple[str, str, str]] = set()
    for ann in db.query(Announcement).filter(Announcement.source == "学院官网").all():
        if classify_slot(ann.title, ann.event_type, ann.status) == slot:
            keys.add((ann.university, ann.college, ann.college_type))
    return keys


def filter_wechat_targets(
    db: Session,
    targets: list,
    board: str,
    phase: str,
    official_hits_this_run: set[tuple[str, str, str]],
) -> list:
    """官网本轮或历史已收录的学院，不再检索微信公众号。"""
    if not settings.wechat_enabled:
        return []

    db_official = colleges_with_official_slot(db, board, phase)
    result = []
    for t in targets:
        key = (t.university, t.college, t.college_type)
        if key in official_hits_this_run or key in db_official:
            continue
        result.append(t)
    skipped = len(targets) - len(result)
    if skipped:
        logger.info(
            "微信检索：跳过 %d 个官网已收录学院，待搜微信 %d 个",
            skipped, len(result),
        )
    return result


def sync_coverage_from_announcements(db: Session) -> None:
    for ann in db.query(Announcement).all():
        slot = classify_slot(ann.title, ann.event_type, ann.status)
        _upsert_coverage(db, ann.university, ann.college, ann.college_type, slot, ann.id)
    db.commit()


def _upsert_coverage(
    db: Session,
    university: str,
    college: str,
    college_type: str,
    slot: str,
    announcement_id: int | None,
    *,
    touch_only: bool = False,
) -> None:
    """写入检索覆盖记录。touch_only=True 时仅更新时间，不覆盖已有 announcement_id。"""
    existing = db.query(CrawlCoverage).filter_by(
        university=university, college=college, college_type=college_type, slot=slot,
    ).first()
    now = datetime.now()
    if existing:
        existing.updated_at = now
        if not touch_only and announcement_id is not None:
            existing.announcement_id = announcement_id
    else:
        db.add(CrawlCoverage(
            university=university,
            college=college,
            college_type=college_type,
            slot=slot,
            announcement_id=announcement_id,
            updated_at=now,
        ))


def mark_target_searched(db: Session, target, board: str, phase: str) -> None:
    """标记某学院某槽位已完成检索（无论是否找到通知），下次爬取跳过。"""
    slot = _slot_for_phase(board, phase)
    _upsert_coverage(
        db,
        target.university,
        target.college,
        target.college_type,
        slot,
        None,
        touch_only=True,
    )


def sync_result_coverage_from_notice(db: Session) -> None:
    """通知槽位已检索的学院，同步标记结果槽位（兼容历史数据）。"""
    from crawler.boards import (
        SLOT_PRE_ADMISSION_NOTICE,
        SLOT_PRE_ADMISSION_RESULT,
        SLOT_SUMMER_CAMP_NOTICE,
        SLOT_SUMMER_CAMP_RESULT,
    )

    pairs = (
        (SLOT_SUMMER_CAMP_NOTICE, SLOT_SUMMER_CAMP_RESULT),
        (SLOT_PRE_ADMISSION_NOTICE, SLOT_PRE_ADMISSION_RESULT),
    )
    for notice_slot, result_slot in pairs:
        rows = db.query(CrawlCoverage).filter(CrawlCoverage.slot == notice_slot).all()
        for row in rows:
            _upsert_coverage(
                db, row.university, row.college, row.college_type, result_slot, None,
                touch_only=True,
            )
    db.commit()


def sync_coverage_from_pending(db: Session) -> None:
    """将待定榜学院同步为已检索覆盖（兼容历史数据）。"""
    from models import CollegePending

    for row in db.query(CollegePending).all():
        _upsert_coverage(
            db, row.university, row.college, row.college_type, row.slot, None,
            touch_only=True,
        )
    db.commit()


def is_target_covered(db: Session, target, board: str, phase: str) -> bool:
    slot = _slot_for_phase(board, phase)
    key = (target.university, target.college, target.college_type, slot)
    covered = get_covered_slots(db, board)
    return key in covered


def mark_coverage(db: Session, ann: Announcement) -> None:
    slot = classify_slot(ann.title, ann.event_type, ann.status)
    _upsert_coverage(db, ann.university, ann.college, ann.college_type, slot, ann.id)


def get_covered_slots(db: Session, board: str) -> set[tuple[str, str, str, str]]:
    slots = BOARD_SLOTS[board]
    rows = db.query(CrawlCoverage).filter(CrawlCoverage.slot.in_(slots)).all()
    return {(r.university, r.college, r.college_type, r.slot) for r in rows}


def filter_targets_for_phase(targets: list, covered: set, board: str, phase: str) -> list:
    slot = _slot_for_phase(board, phase)

    return [
        t for t in targets
        if (t.university, t.college, t.college_type, slot) not in covered
    ]
