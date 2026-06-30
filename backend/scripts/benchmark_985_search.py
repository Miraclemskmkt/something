"""985 检索速度实测（不写库）。"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from crawler.boards import SUMMER_CAMP
from crawler.pending import list_pending
from crawler.searcher import search_for_targets
from crawler.university_config import UNIVERSITY_TARGETS
from database import SessionLocal
from tier_filter import universities_in_tier


async def main() -> None:
    db = SessionLocal()
    pending = list_pending(db, SUMMER_CAMP, tier="985")
    print(f"985 待定学院: {len(pending)}")

    keys = {(p.university, p.college, p.college_type) for p in pending}
    targets = [t for t in UNIVERSITY_TARGETS if (t.university, t.college, t.college_type) in keys]

    if len(targets) < 5:
        u985 = universities_in_tier("985")
        targets = [t for t in UNIVERSITY_TARGETS if t.university in u985][:12]
        print(f"待定不足，改测 12 所样本")
    else:
        targets = targets[:30]
        print(f"实测待定样本: {len(targets)}")

    print(
        f"fast={settings.crawl_fast_mode} "
        f"concurrent={settings.search_max_concurrent} "
        f"delay={settings.search_request_delay}s"
    )

    t0 = time.perf_counter()
    items = await search_for_targets(targets, SUMMER_CAMP, "notice")
    elapsed = time.perf_counter() - t0

    print(f"\n=== 结果 ===")
    print(f"学院数: {len(targets)}")
    print(f"耗时: {elapsed:.1f}s ({elapsed / 60:.1f} min)")
    print(f"平均每学院: {elapsed / max(len(targets), 1):.1f}s")
    print(f"命中通知: {len(items)} 条")
    for it in items[:10]:
        title = (it.title or "")[:55]
        print(f"  [{it.college_type}] {it.university} · {it.college}")
        print(f"       {title}")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
