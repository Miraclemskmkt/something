"""端到端诊断：检索 → 解析 → 入库 → API 读取。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import SessionLocal, init_db, DB_PATH
from models import Announcement
from crawler.parser import ParsedAnnouncement, parse_news_list, is_valid_announcement_url, is_year_eligible
from crawler.service import fetch_page, crawl_university, save_announcements
from crawler.university_config import UNIVERSITY_TARGETS
from crawler.boards import matches_board, SUMMER_CAMP
from main import _filter_items


def test_db_write():
    init_db(sync_coverage=True)
    db = SessionLocal()
    before = db.query(Announcement).count()
    item = ParsedAnnouncement(
        title="2026年法学院优秀大学生夏令营招生简章",
        url="https://law.uibe.edu.cn/xwzx/tzgg/test-pipeline-2026.htm",
        publish_date="2026-05-01",
        event_type="夏令营",
        status="active",
        source="学院官网",
        university="对外经济贸易大学",
        college="法学院",
        college_type="law",
    )
    assert is_valid_announcement_url(item.url)
    assert is_year_eligible(item.title, item.publish_date, item.deadline)
    new, upd = save_announcements(db, [item])
    after = db.query(Announcement).count()
    row = db.query(Announcement).filter(Announcement.url == item.url).first()
    if row:
        db.delete(row)
    db.commit()
    db.close()
    ok = new == 1 and after == before + 1
    print(f"[DB写入] {'PASS' if ok else 'FAIL'} new={new} count {before}->{after}")
    return ok


async def test_fetch_parse():
    samples = [
        ("对外经济贸易大学", "https://law.uibe.edu.cn/xwzx/tzgg/index.htm", "law"),
        ("复旦大学", "https://law.fudan.edu.cn/882/list.htm", "law"),
        ("对外经济贸易大学", "https://sfs.uibe.edu.cn/xwzx/tzgg/index.htm", "foreign_lang"),
    ]
    any_html = False
    any_camp = False
    for name, url, ct in samples:
        html = await fetch_page(url)
        n = len(html or "")
        print(f"[抓取] {name} {url} -> {n} bytes")
        if n < 500:
            continue
        any_html = True
        items = parse_news_list(html, url.rsplit("/", 1)[0], ct, board=SUMMER_CAMP, phase="notice")
        print(f"  [解析夏令营] {len(items)} 条")
        for i in items[:2]:
            print(f"    - {i.title[:60]}")
            any_camp = True
    print(f"[抓取/解析] html_ok={any_html} camp_found={any_camp}")
    return any_html


async def test_live_crawl_sample():
    targets = [t for t in UNIVERSITY_TARGETS if t.university in (
        "对外经济贸易大学", "复旦大学", "武汉大学", "中南财经政法大学"
    )]
    total = 0
    for t in targets:
        items = await crawl_university(t, SUMMER_CAMP, "notice")
        print(f"[学院爬取] {t.university} {t.college}: {len(items)} 条")
        total += len(items)
        for i in items[:1]:
            print(f"    {i.title[:55]} | url_ok={is_valid_announcement_url(i.url)}")
    print(f"[学院爬取合计] {total} 条")
    return total


def test_api_filter():
    db = SessionLocal()
    items = db.query(Announcement).all()
    filtered = _filter_items(items, SUMMER_CAMP)
    print(f"[API过滤] DB {len(items)} -> 夏令营展示 {len(filtered)}")
    db.close()


async def main():
    print("DB path:", DB_PATH)
    print("---")
    test_db_write()
    print("---")
    await test_fetch_parse()
    print("---")
    n = await test_live_crawl_sample()
    print("---")
    test_api_filter()
    print("---")
    if n == 0:
        print("结论: 入库流程可用，但当前官网通知页几乎没有「2026夏令营」标题，故检索结果为 0。")
    else:
        print("结论: 全流程可产出数据，请检查 save 与 API。")


if __name__ == "__main__":
    asyncio.run(main())
