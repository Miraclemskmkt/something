"""全量统一重检：导出用户补全快照 → 清除缓存 → 检索入库 → 恢复快照 → 生成失败表。"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from crawler.boards import BOARD_LABELS, SUMMER_CAMP
from crawler.manual_preserves import (
    MANUAL_SOURCE,
    export_manual_preserves,
    is_manual_key,
    restore_manual_preserves,
)
from crawler.parser import core_times_complete
from crawler.service import run_crawl
from crawler.university_config import UNIVERSITY_TARGETS
from database import SessionLocal, init_db
from models import Announcement, CollegePending, CrawlCoverage
from tier_filter import UNIVERSITY_TIER, resolve_tier

REPORT_FILE = Path(__file__).resolve().parent.parent / "data" / "crawl_failure_report.txt"


async def probe_homepage(url: str) -> str:
    from crawler.fetcher import fetch_page

    if not url:
        return "无官网"
    html = await fetch_page(url, fast=True)
    if not html:
        return "官网不可达"
    if len(html) < 200:
        return "响应过短"
    return "官网可达"


async def build_failure_report(db) -> str:
    from crawler.notice_calendar import in_activation_window
    from crawler.pending_kinds import KIND_LABELS

    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"夏令营待定/字段不全报告  生成时间: {now}")
    lines.append("=" * 72)
    lines.append("")

    pending = db.query(CollegePending).filter(
        CollegePending.slot == "summer_camp_notice",
    ).order_by(CollegePending.university).all()

    target_map = {
        (t.university, t.college, t.college_type): t for t in UNIVERSITY_TARGETS
    }

    by_kind: dict[str, list] = {}
    for p in pending:
        by_kind.setdefault(p.pending_kind or "not_published", []).append(p)

    lines.append(f"【待定汇总】共 {len(pending)} 所")
    for kind, rows in sorted(by_kind.items(), key=lambda x: -len(x[1])):
        label = KIND_LABELS.get(kind, kind)
        lines.append(f"  - {label}: {len(rows)} 所")
    lines.append("")

    for kind in ("domain_failure", "not_published", "watching"):
        rows = by_kind.get(kind, [])
        if not rows:
            continue
        label = KIND_LABELS.get(kind, kind)
        lines.append(f"【{label}】{len(rows)} 所")
        lines.append(
            f"{'分层':<6} {'学校':<14} {'学院':<12} {'探测':<8} {'下次检查':<12} 官网"
        )
        lines.append("-" * 72)
        for p in rows:
            tier = UNIVERSITY_TIER.get(p.university, "-")
            tgt = target_map.get((p.university, p.college, p.college_type))
            hp = tgt.base_url if tgt else ""
            dom = p.domain_status or "-"
            nxt = p.next_check_at.strftime("%m-%d") if p.next_check_at else "-"
            if kind == "not_published" and not in_activation_window(
                p.university, p.college_type, "summer_camp",
            ):
                nxt = "窗口外"
            lines.append(f"{tier:<6} {p.university:<14} {p.college:<12} {dom:<8} {nxt:<12} {hp}")
        lines.append("")

    rows_incomplete: list[tuple] = []
    for a in db.query(Announcement).all():
        if core_times_complete(a):
            continue
        key = (a.university, a.college, a.college_type)
        tier = UNIVERSITY_TIER.get(a.university, "-")
        missing = []
        if not a.publish_date:
            missing.append("开放")
        if not a.deadline:
            missing.append("截止")
        if not a.event_time:
            missing.append("举办时间")
        if not a.event_format:
            missing.append("形式")
        manual = "是(已保护)" if is_manual_key(key) else "否"
        rows_incomplete.append((
            tier, a.university, a.college,
            "、".join(missing) or "缺字段",
            (a.title or "")[:40],
            a.url[:60] if a.url else "",
            manual,
        ))

    lines.append(f"【已入库但字段不全】共 {len(rows_incomplete)} 条 → 见 data/incomplete_manual_queue.json")
    lines.append(
        f"{'分层':<6} {'学校':<14} {'学院':<12} {'缺失':<16} {'标题':<28} 用户补全"
    )
    lines.append("-" * 72)
    for tier, uni, col, miss, title, url, manual in rows_incomplete:
        lines.append(f"{tier:<6} {uni:<14} {col:<12} {miss:<16} {title:<28} {manual}")
        if url:
            lines.append(f"       URL: {url}")

    text = "\n".join(lines)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(text, encoding="utf-8")
    return text


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", default="summer_camp", choices=["summer_camp", "pre_admission"])
    args = parser.parse_args()

    init_db(sync_coverage=True)
    db = SessionLocal()

    for ann in db.query(Announcement).all():
        key = (ann.university, ann.college, ann.college_type)
        if is_manual_key(key):
            ann.source = MANUAL_SOURCE
    db.commit()

    n = export_manual_preserves(db)
    before = db.query(Announcement).count()
    print(f"已导出 {n} 条保护快照，库内通知 {before} 条")

    from crawler.coverage import clear_tier_board_coverage

    cov, pend = clear_tier_board_coverage(db, None, args.board, phases=("notice",))
    print(f"已清除全部覆盖 {cov} 条、待定 {pend} 条")
    db.close()

    label = BOARD_LABELS.get(args.board, args.board)
    print(f"\n=== 开始全量重检：{label}，目标学院 {len(UNIVERSITY_TARGETS)} ===")
    t0 = time.perf_counter()
    result = await run_crawl(args.board, tier=None, refresh=True)
    elapsed = time.perf_counter() - t0
    print(f"检索耗时: {elapsed:.1f}s ({elapsed / 60:.1f} min)")
    print(f"结果: {result}")

    db = SessionLocal()
    restored, merged = restore_manual_preserves(db)
    from crawler.coverage import sync_coverage_from_announcements, sync_coverage_from_pending

    sync_coverage_from_announcements(db)
    sync_coverage_from_pending(db)
    from crawler.pending import clear_pending_for_announcement

    for ann in db.query(Announcement).all():
        clear_pending_for_announcement(db, ann)
    db.commit()

    from crawler.incomplete_manual_queue import export_incomplete_queue

    export_incomplete_queue(db)
    if settings.llm_enabled:
        from crawler.incomplete_fixer import fix_incomplete_announcements
        fixed = await fix_incomplete_announcements(db)
        print(f"LLM 字段补全: {fixed} 条")
    else:
        print("字段不全人工队列已导出 → data/incomplete_manual_queue.json（LLM 未启用）")

    after = db.query(Announcement).count()
    complete = sum(1 for a in db.query(Announcement).all() if core_times_complete(a))
    print(f"\n快照恢复: 新增 {restored}，合并 {merged}")
    print(f"库内通知: {before} → {after}，四字段齐全 {complete} 条")

    report = await build_failure_report(db)
    db.close()
    print(f"\n失败/待定报告已写入: {REPORT_FILE}")
    print(report[:2500])


if __name__ == "__main__":
    asyncio.run(main())
