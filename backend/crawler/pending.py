"""学院检索待定榜：已检索但未发现对应通知的学院。"""

from datetime import datetime

from sqlalchemy.orm import Session

from crawler.boards import PRE_ADMISSION, SUMMER_CAMP
from models import Announcement, CollegePending

NOTICE_SLOTS = {
    (SUMMER_CAMP, "notice"): "summer_camp_notice",
    (PRE_ADMISSION, "notice"): "pre_admission_notice",
}


def notice_slot(board: str, phase: str) -> str | None:
    return NOTICE_SLOTS.get((board, phase))


def pending_title(university: str, college: str, board: str) -> str:
    if board == PRE_ADMISSION:
        return f"{university} · {college} · 暂未检索到2026年预推免通知"
    return f"{university} · {college} · 暂未检索到2026年夏令营通知"


def mark_college_pending(db: Session, target, board: str, phase: str) -> None:
    slot = notice_slot(board, phase)
    if not slot:
        return
    existing = db.query(CollegePending).filter_by(
        university=target.university,
        college=target.college,
        college_type=target.college_type,
        slot=slot,
    ).first()
    now = datetime.now()
    if existing:
        existing.search_count += 1
        existing.updated_at = now
    else:
        db.add(CollegePending(
            university=target.university,
            college=target.college,
            college_type=target.college_type,
            slot=slot,
            search_count=1,
            updated_at=now,
        ))


def clear_college_pending(db: Session, target, board: str, phase: str) -> None:
    slot = notice_slot(board, phase)
    if not slot:
        return
    db.query(CollegePending).filter_by(
        university=target.university,
        college=target.college,
        college_type=target.college_type,
        slot=slot,
    ).delete(synchronize_session=False)


def clear_pending_for_announcement(db: Session, ann: Announcement) -> None:
    from crawler.boards import classify_slot

    slot = classify_slot(ann.title, ann.event_type, ann.status)
    if slot not in ("summer_camp_notice", "pre_admission_notice"):
        return
    db.query(CollegePending).filter_by(
        university=ann.university,
        college=ann.college,
        college_type=ann.college_type,
        slot=slot,
    ).delete(synchronize_session=False)


def list_pending(
    db: Session,
    board: str,
    college_type: str | None = None,
    search: str | None = None,
    tier: str | None = None,
) -> list[CollegePending]:
    from crawler.boards import SLOT_PRE_ADMISSION_NOTICE, SLOT_SUMMER_CAMP_NOTICE
    from tier_filter import universities_in_tier

    slot = SLOT_SUMMER_CAMP_NOTICE if board == SUMMER_CAMP else SLOT_PRE_ADMISSION_NOTICE
    q = db.query(CollegePending).filter(CollegePending.slot == slot)
    if college_type:
        q = q.filter(CollegePending.college_type == college_type)
    if tier:
        q = q.filter(CollegePending.university.in_(universities_in_tier(tier)))
    if search:
        q = q.filter(
            CollegePending.university.contains(search)
            | CollegePending.college.contains(search)
        )
    return q.order_by(CollegePending.updated_at.desc()).all()
