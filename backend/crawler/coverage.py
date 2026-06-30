"""学院检索覆盖状态：已找到的通知类型不再重复爬取。"""

import logging
import threading
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from crawler.boards import (
    BOARD_SLOTS,
    PRE_ADMISSION,
    SUMMER_CAMP,
    classify_slot,
)
from crawler.source_labels import is_official_source, is_wechat_source
from models import Announcement, CrawlCoverage

logger = logging.getLogger(__name__)

_db_write_lock = threading.Lock()


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
    for ann in db.query(Announcement).all():
        if not is_official_source(ann.source):
            continue
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
    """
    搜狗微信搜索目标筛选：
    - 官网已收录 → 跳过
    - pending_only 模式 → 仅待定/域名失败学院
    - 已有微信公众号来源 → 跳过
    - 今日已搜狗 → 跳过
    """
    if not settings.wechat_enabled:
        return []

    from crawler.wechat_state import college_searched_today
    from models import CollegePending

    db_official = colleges_with_official_slot(db, board, phase)
    db_wechat = colleges_with_wechat_slot(db, board, phase)

    pending_keys: set[tuple[str, str, str]] = set()
    if settings.wechat_pending_only:
        slot = _slot_for_phase(board, phase)
        for p in db.query(CollegePending).filter_by(slot=slot).all():
            pending_keys.add((p.university, p.college, p.college_type))

    result = []
    for t in targets:
        key = (t.university, t.college, t.college_type)
        if key in official_hits_this_run or key in db_official:
            continue
        if key in db_wechat:
            continue
        if settings.wechat_pending_only and pending_keys and key not in pending_keys:
            continue
        if college_searched_today(t.university, t.college):
            continue
        result.append(t)

    skipped = len(targets) - len(result)
    if skipped:
        logger.info(
            "搜狗微信：跳过 %d 个（官网/已有微信/非待定/今日已搜），待搜 %d 个",
            skipped, len(result),
        )
    return result


def colleges_with_wechat_slot(db: Session, board: str, phase: str) -> set[tuple[str, str, str]]:
    """已有微信公众号来源通知的学院。"""
    slot = _slot_for_phase(board, phase)
    keys: set[tuple[str, str, str]] = set()
    for ann in db.query(Announcement).all():
        if not is_wechat_source(ann.source):
            continue
        from crawler.boards import classify_slot
        if classify_slot(ann.title, ann.event_type, ann.status) == slot:
            keys.add((ann.university, ann.college, ann.college_type))
    return keys


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
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.query(CrawlCoverage).filter_by(
                university=university, college=college, college_type=college_type, slot=slot,
            ).first()
            if existing:
                existing.updated_at = now
                if not touch_only and announcement_id is not None:
                    existing.announcement_id = announcement_id


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


def notice_slot_for_board(board: str) -> str:
    return _slot_for_phase(board, "notice")


def clear_tier_board_coverage(
    db: Session,
    tier: str | None,
    board: str,
    *,
    phases: tuple[str, ...] = ("notice",),
) -> tuple[int, int]:
    """清除某分层在某板块下的检索覆盖与待定记录，用于网页「刷新检索」。tier=None 表示全部。"""
    from models import CollegePending
    from tier_filter import universities_in_tier

    tier_unis = list(universities_in_tier(tier)) if tier else None
    slots = [_slot_for_phase(board, p) for p in phases]
    cov_q = db.query(CrawlCoverage).filter(CrawlCoverage.slot.in_(slots))
    pend_q = db.query(CollegePending).filter(CollegePending.slot.in_(slots))
    if tier_unis is not None:
        cov_q = cov_q.filter(CrawlCoverage.university.in_(tier_unis))
        pend_q = pend_q.filter(CollegePending.university.in_(tier_unis))
    cov_del = cov_q.delete(synchronize_session=False)
    pend_del = pend_q.delete(synchronize_session=False)
    db.commit()
    logger.info(
        "已清除 %s · %s 覆盖 %d 条、待定 %d 条（阶段 %s）",
        tier or "全部", board, cov_del, pend_del, phases,
    )
    return cov_del, pend_del
