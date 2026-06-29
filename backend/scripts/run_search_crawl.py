"""Run search crawl with per-college incremental writes."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.service import IncrementalSaver, crawl_board_phase
from crawler.university_config import UNIVERSITY_TARGETS
from database import SessionLocal, init_db
from models import CrawlLog


def parse_args():
    p = argparse.ArgumentParser(description="全网检索 + 每学院写入 + 待定榜")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--board", default="summer_camp", choices=["summer_camp", "pre_admission"])
    p.add_argument("--phase", default="notice", choices=["notice", "result"])
    return p.parse_args()


async def main():
    init_db()
    args = parse_args()
    targets = UNIVERSITY_TARGETS[: args.limit] if args.limit > 0 else UNIVERSITY_TARGETS
    db = SessionLocal()
    log = CrawlLog(status="running", board=args.board)
    saver = IncrementalSaver(db, log, args.board)
    db.add(log)
    db.commit()
    try:
        items = await crawl_board_phase(targets, args.board, args.phase, db, saver=saver)
        new, upd = saver.finish()
        log.status = "success"
        log.found_count = len(items)
        log.new_count = new
        log.updated_count = upd
        log.message = f"完成：通知 {len(items)} 条，新增 {new}"
        db.commit()
        print(log.message)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
