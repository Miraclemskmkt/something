"""按待定类型与通知日历过滤待爬学院。"""
from __future__ import annotations

from crawler.boards import SUMMER_CAMP
from crawler.pending_classifier import should_crawl_target
from crawler.pending_kinds import DOMAIN_FAILURE
from models import CollegePending


def filter_targets_by_schedule(
    db,
    targets: list,
    board: str,
    phase: str,
) -> list:
    """窗口外 / 域名失效的学院本轮不爬。"""
    if phase != "notice":
        return targets

    from crawler.pending import notice_slot

    slot = notice_slot(board, phase)
    if not slot:
        return targets

    pending_rows = db.query(CollegePending).filter(CollegePending.slot == slot).all()
    pending_map = {
        (p.university, p.college, p.college_type): p for p in pending_rows
    }

    kept, skipped_dormant, skipped_domain = [], 0, 0
    for t in targets:
        p = pending_map.get((t.university, t.college, t.college_type))
        if not p:
            kept.append(t)
            continue
        if p.pending_kind == DOMAIN_FAILURE:
            skipped_domain += 1
            continue
        if should_crawl_target(
            p.pending_kind,
            p.next_check_at,
            t.university,
            t.college_type,
            board,
        ):
            kept.append(t)
        else:
            skipped_dormant += 1

    if skipped_dormant or skipped_domain:
        import logging
        logging.getLogger(__name__).info(
            "日历过滤：跳过 dormant=%d domain_failure=%d，待爬=%d",
            skipped_dormant, skipped_domain, len(kept),
        )
    return kept
