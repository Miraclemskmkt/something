"""打印某分层入库与待定统计。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.boards import PRE_ADMISSION, SUMMER_CAMP, matches_board
from database import SessionLocal
from models import Announcement, CollegePending, CrawlCoverage, CrawlLog
from tier_filter import universities_in_tier


def main(tier: str = "211") -> None:
    u = universities_in_tier(tier)
    db = SessionLocal()
    rows = [a for a in db.query(Announcement).all() if a.university in u]
    print(f"=== {tier} 已入库通知 {len(rows)} ===")
    for a in sorted(rows, key=lambda x: (x.university, x.college)):
        board = "预推免" if matches_board(a, PRE_ADMISSION) else "夏令营"
        title = (a.title or "")[:60]
        print(f"[{board}] {a.university} · {a.college}")
        print(f"  {title}")
        print(f"  截止: {a.deadline or '—'}")

    pending = db.query(CollegePending).filter(CollegePending.university.in_(u)).all()
    print(f"\n=== {tier} 待定学院 {len(pending)} ===")
    for p in sorted(pending, key=lambda x: (x.university, x.college))[:15]:
        print(f"  {p.university} · {p.college} ({p.slot})")
    if len(pending) > 15:
        print(f"  ... 共 {len(pending)} 所")

    cov = db.query(CrawlCoverage).filter(CrawlCoverage.university.in_(u)).count()
    print(f"\n覆盖缓存: {cov} 条")

    logs = db.query(CrawlLog).order_by(CrawlLog.id.desc()).limit(4).all()
    print("\n=== 最近检索日志 ===")
    for lg in logs:
        print(f"  {lg.board} | 发现{lg.found_count} 新增{lg.new_count} | {(lg.message or '')[:70]}")
    db.close()


if __name__ == "__main__":
    tier = sys.argv[1] if len(sys.argv) > 1 else "211"
    main(tier)
