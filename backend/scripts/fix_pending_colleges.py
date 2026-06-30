"""待定学院治理：分类、洗域名、仅修域名失效（禁止全量补抓）。"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.boards import SUMMER_CAMP
from crawler.domain_fixer import batch_fix_domains
from crawler.domain_overrides import clean_invalid_overrides
from crawler.fetcher import create_http_client
from crawler.incomplete_manual_queue import export_incomplete_queue
from crawler.notice_calendar import in_activation_window, next_check_datetime
from crawler.pending_classifier import classify_college
from crawler.pending_kinds import DOMAIN_FAILURE, KIND_LABELS, NOT_PUBLISHED, WATCHING
from crawler.pending_recrawl import batch_recrawl, refresh_targets
from crawler.service import save_announcements
from crawler.university_config import UNIVERSITY_TARGETS
from database import SessionLocal, init_db
from models import Announcement, CollegePending
from scripts.run_full_refresh import build_failure_report


async def classify_all_pending(db, *, concurrency: int = 16) -> None:
    pending = db.query(CollegePending).filter(
        CollegePending.slot == "summer_camp_notice",
    ).all()
    target_map = {
        (t.university, t.college, t.college_type): t for t in UNIVERSITY_TARGETS
    }
    sem = asyncio.Semaphore(concurrency)

    async with create_http_client() as client:
        async def one(p: CollegePending):
            t = target_map.get((p.university, p.college, p.college_type))
            if not t:
                return
            async with sem:
                kind, dom, nxt = await classify_college(t, SUMMER_CAMP, client)
            p.pending_kind = kind
            p.domain_status = dom
            p.next_check_at = nxt
            p.updated_at = datetime.now()

        await asyncio.gather(*[one(p) for p in pending])
    db.commit()


async def fix_domain_failures_only(db) -> list[tuple[str, str, str, str]]:
    rows = db.query(CollegePending).filter(
        CollegePending.slot == "summer_camp_notice",
        CollegePending.pending_kind == DOMAIN_FAILURE,
    ).all()
    target_map = {
        (t.university, t.college, t.college_type): t for t in UNIVERSITY_TARGETS
    }
    targets = [
        target_map[(p.university, p.college, p.college_type)]
        for p in rows
        if (p.university, p.college, p.college_type) in target_map
    ]
    if not targets:
        return []

    async with create_http_client() as c:
        fixed = await batch_fix_domains(targets, c, force_aliases=True)

    refresh_targets()
    now = datetime.now()
    for p in rows:
        key = (p.university, p.college, p.college_type)
        if any(f[0] == p.university and f[1] == p.college for f in fixed):
            p.pending_kind = WATCHING
            p.domain_status = "reachable"
            p.next_check_at = next_check_datetime(p.university, p.college_type, SUMMER_CAMP)
        elif p.next_check_at and now >= p.next_check_at:
            p.next_check_at = now + timedelta(days=7)
    db.commit()
    return fixed


async def recrawl_in_window_only(db) -> int:
    """仅对日历窗口内、非 domain_failure 的学院轻量补抓。"""
    pending = db.query(CollegePending).filter(
        CollegePending.slot == "summer_camp_notice",
        CollegePending.pending_kind.in_([NOT_PUBLISHED, WATCHING]),
    ).all()
    target_map = {
        (t.university, t.college, t.college_type): t for t in UNIVERSITY_TARGETS
    }
    targets = []
    for p in pending:
        if not in_activation_window(p.university, p.college_type, SUMMER_CAMP):
            continue
        if p.next_check_at and datetime.now() < p.next_check_at:
            continue
        t = target_map.get((p.university, p.college, p.college_type))
        if t:
            targets.append(t)
    if not targets:
        return 0

    found = await batch_recrawl(targets, SUMMER_CAMP, "notice")
    flat = [it for items in found.values() for it in items]
    if flat:
        save_announcements(db, flat)
        db.commit()
    return len(flat)


async def main() -> None:
    parser = argparse.ArgumentParser(description="待定治理（非全量补抓）")
    parser.add_argument("--classify", action="store_true", help="重新探测并分类全部待定")
    parser.add_argument("--fix-domains", action="store_true", help="仅修正 domain_failure 学院")
    parser.add_argument("--recrawl-window", action="store_true", help="仅窗口内学院轻量补抓")
    parser.add_argument("--all", action="store_true", help="分类+洗域名+修域名（默认）")
    args = parser.parse_args()

    run_all = args.all or not (args.classify or args.fix_domains or args.recrawl_window)

    init_db()
    db = SessionLocal()

    removed = clean_invalid_overrides()
    print(f"清洗无效 domain_overrides: {len(removed)} 条")
    for uni, col, _, url, reason in removed[:10]:
        print(f"  删 {uni} {col}: {url} ({reason})")

    refresh_targets()

    if run_all or args.classify:
        print("分类待定学院…")
        await classify_all_pending(db)
        kinds = {}
        for p in db.query(CollegePending).filter_by(slot="summer_camp_notice"):
            kinds[p.pending_kind] = kinds.get(p.pending_kind, 0) + 1
        print("分类结果:", {KIND_LABELS.get(k, k): v for k, v in kinds.items()})

    if run_all or args.fix_domains:
        fixed = await fix_domain_failures_only(db)
        print(f"域名失效修正: {len(fixed)} 所")

    if args.recrawl_window:
        n = await recrawl_in_window_only(db)
        print(f"窗口内补抓命中: {n} 条")

    incomplete = export_incomplete_queue(db)
    print(f"字段不全人工队列: {len(incomplete)} 条 → data/incomplete_manual_queue.json")

    report = await build_failure_report(db)
    db.close()
    print("失败报告已更新（含分类）")
    print(report[:1800])


if __name__ == "__main__":
    asyncio.run(main())
