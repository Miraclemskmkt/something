"""检索全部 985 高校法学院 + 外国语学院夏令营（含非法本）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from crawler.boards import SUMMER_CAMP
from crawler.service import IncrementalSaver, crawl_board_phase
from crawler.university_config import UNIVERSITY_TARGETS
from database import SessionLocal, init_db
from institutions_data import INSTITUTIONS
from models import Announcement, CollegePending, CrawlLog


def get_985_targets():
    uni_985 = {i.university for i in INSTITUTIONS if "985" in i.tags}
    return [t for t in UNIVERSITY_TARGETS if t.university in uni_985]


async def main():
    init_db()
    settings.search_max_per_target = 3
    settings.search_compact_keywords = False

    targets = get_985_targets()
    law_n = sum(1 for t in targets if t.college_type == "law")
    fl_n = sum(1 for t in targets if t.college_type == "foreign_lang")
    print(f"985 目标：共 {len(targets)} 个学院（法学院 {law_n}，外国语 {fl_n}）")

    db = SessionLocal()
    log = CrawlLog(status="running", board=SUMMER_CAMP)
    saver = IncrementalSaver(db, log, SUMMER_CAMP)
    db.add(log)
    db.commit()

    try:
        items = await crawl_board_phase(
            targets, SUMMER_CAMP, "notice", db, saver=saver,
        )
        new, upd = saver.finish()
        log.status = "success"
        log.found_count = len(items)
        log.new_count = new
        log.updated_count = upd
        log.message = f"985夏令营：通知 {len(items)} 条，新增 {new}"
        db.commit()

        pending = db.query(CollegePending).filter(
            CollegePending.slot == "summer_camp_notice",
            CollegePending.university.in_({t.university for t in targets}),
        ).all()
        pending_keys = {(p.university, p.college, p.college_type) for p in pending}

        print(f"\n=== 检索完成 ===")
        print(f"有通知: {len(items)} 条（新增 {new}，更新 {upd}）")
        print(f"待定: {len(pending_keys)} 个学院\n")

        if items:
            print("--- 已发现夏令营通知 ---")
            for item in sorted(items, key=lambda x: (x.university, x.college_type)):
                tag = "法学" if item.college_type == "law" else "外语"
                print(f"  [{tag}] {item.university} · {item.college}")
                print(f"       {item.title[:70]}")
                print(f"       {item.url}")

        print("\n--- 待定（暂未检索到通知）---")
        for p in sorted(pending, key=lambda x: (x.university, x.college_type)):
            tag = "法学" if p.college_type == "law" else "外语"
            print(f"  [{tag}] {p.university} · {p.college}")

        # 写入报告文件
        report = Path(__file__).parent / "985_summer_camp_report.txt"
        with report.open("w", encoding="utf-8") as f:
            f.write(f"985 夏令营检索报告\n通知 {len(items)} 条，待定 {len(pending_keys)} 个学院\n\n")
            f.write("【有通知】\n")
            for item in sorted(items, key=lambda x: (x.university, x.college_type)):
                f.write(f"{item.university}\t{item.college}\t{item.title}\t{item.url}\n")
            f.write("\n【待定】\n")
            for p in sorted(pending, key=lambda x: (x.university, x.college_type)):
                f.write(f"{p.university}\t{p.college}\t{p.college_type}\n")
        print(f"\n报告已保存: {report}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
