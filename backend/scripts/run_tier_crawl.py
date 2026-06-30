"""按分层跑夏令营/预推免检索并入库。"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.boards import BOARD_LABELS, BOARD_SLOTS, PRE_ADMISSION, SUMMER_CAMP
from crawler.service import run_crawl
from crawler.university_config import UNIVERSITY_TARGETS
from database import SessionLocal, init_db
from models import Announcement, CrawlCoverage, CollegePending
from tier_filter import filter_targets_by_tier, universities_in_tier


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--tier", required=True, choices=["985", "211", "双一流"])
    p.add_argument(
        "--boards",
        default="both",
        choices=["summer_camp", "pre_admission", "both"],
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="清除该分层覆盖缓存后重跑",
    )
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    init_db(sync_coverage=True)
    boards = [SUMMER_CAMP, PRE_ADMISSION] if args.boards == "both" else [args.boards]
    tier_unis = universities_in_tier(args.tier)
    pool = filter_targets_by_tier(UNIVERSITY_TARGETS, args.tier)

    db = SessionLocal()
    before = db.query(Announcement).count()

    if args.reset:
        slots: list[str] = []
        for b in boards:
            slots.extend(BOARD_SLOTS[b])
        cov_del = db.query(CrawlCoverage).filter(
            CrawlCoverage.university.in_(tier_unis),
            CrawlCoverage.slot.in_(slots),
        ).delete(synchronize_session=False)
        pend_del = db.query(CollegePending).filter(
            CollegePending.university.in_(tier_unis),
            CollegePending.slot.in_(slots),
        ).delete(synchronize_session=False)
        db.commit()
        print(f"已清除缓存 {cov_del} 条、待定 {pend_del} 条")

    db.close()

    print(f"=== {args.tier} 检索入库 ===")
    print(f"目标学院: {len(pool)}")
    print(f"入库前通知数: {before}")
    t_all = time.perf_counter()

    for board in boards:
        label = BOARD_LABELS.get(board, board)
        print(f"\n--- 开始 {label} ({args.tier}) ---")
        t0 = time.perf_counter()
        result = await run_crawl(board, tier=args.tier)
        elapsed = time.perf_counter() - t0
        print(f"耗时: {elapsed:.1f}s ({elapsed / 60:.1f}min)")
        print(f"结果: {result}")

    db = SessionLocal()
    after = db.query(Announcement).count()
    tier_rows = (
        db.query(Announcement)
        .filter(Announcement.university.in_(list(__import__('tier_filter').universities_in_tier(args.tier))))
        .count()
    )
    db.close()

    total = time.perf_counter() - t_all
    print(f"\n=== 全部完成 ===")
    print(f"总耗时: {total:.1f}s ({total / 60:.1f}min)")
    print(f"库内通知: {before} → {after}（+{after - before}）")
    print(f"{args.tier} 相关记录: {tier_rows} 条")


if __name__ == "__main__":
    asyncio.run(main())
