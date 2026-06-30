"""微信通道定时任务：RSS 拉取（主通道）+ 搜狗扫描（默认关闭）。"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from config import settings
from crawler.boards import SUMMER_CAMP
from crawler.coverage import filter_wechat_targets, sync_coverage_from_announcements
from crawler.service import save_announcements
from tier_filter import filter_targets_by_tier
from crawler.university_config import UNIVERSITY_TARGETS
from crawler.wechat import crawl_wechat_for_targets
from crawler.wechat_rss import crawl_wechat_rss
from crawler.baoyan_sites import crawl_baoyan_sites, enrich_baoyan_items
from database import SessionLocal, init_db

logger = logging.getLogger(__name__)


async def run_wechat_rss_crawl(db: Session | None = None) -> tuple[int, int]:
    """拉取保研公众号 RSS，抓取正文 + LLM 补全后入库。返回 (发现数, 新增数)。"""
    if not settings.wechat_rss_enabled:
        return 0, 0
    own_db = db is None
    if own_db:
        init_db()
        db = SessionLocal()
    try:
        items = await crawl_wechat_rss(board=SUMMER_CAMP, phase="notice")
        if not items:
            return 0, 0
        from crawler.fetcher import create_http_client
        from crawler.wechat import enrich_wechat_items

        async with create_http_client() as client:
            items = await enrich_wechat_items(
                items, client=client, board=SUMMER_CAMP, phase="notice",
            )
        items = [i for i in items if not i.llm_rejected]
        if not items:
            return 0, 0
        new, _ = save_announcements(db, items)
        sync_coverage_from_announcements(db)
        db.commit()
        logger.info("微信 RSS 入库：发现 %d，新增 %d", len(items), new)
        return len(items), new
    finally:
        if own_db and db:
            db.close()


async def run_wechat_sogou_scan(
    db: Session | None = None, tier: str = "all", *, force: bool = False,
) -> tuple[int, int]:
    """对待定学院做搜狗微信搜索（默认关闭，易触发验证码）。"""
    if not settings.wechat_sogou_enabled and not force:
        logger.debug("搜狗微信已关闭（CAMP_WECHAT_SOGOU_ENABLED=false）")
        return 0, 0
    if not settings.wechat_enabled:
        return 0, 0
    own_db = db is None
    if own_db:
        init_db()
        db = SessionLocal()
    try:
        targets = filter_targets_by_tier(UNIVERSITY_TARGETS, tier)
        wechat_targets = filter_wechat_targets(db, targets, SUMMER_CAMP, "notice", set())
        if not wechat_targets:
            logger.info("搜狗微信：无待扫描学院")
            return 0, 0
        items = await crawl_wechat_for_targets(wechat_targets, SUMMER_CAMP, "notice", force=force)
        items = [i for i in items if not i.llm_rejected]
        if not items:
            return 0, 0
        new, _ = save_announcements(db, items)
        sync_coverage_from_announcements(db)
        db.commit()
        logger.info("搜狗微信入库：发现 %d，新增 %d", len(items), new)
        return len(items), new
    finally:
        if own_db and db:
            db.close()


async def run_baoyan_sites_crawl(db: Session | None = None) -> tuple[int, int]:
    """抓取保研论坛等聚合站，匹配法学/外语通知后入库。"""
    if not settings.baoyan_sites_enabled:
        return 0, 0
    own_db = db is None
    if own_db:
        init_db()
        db = SessionLocal()
    try:
        items = await crawl_baoyan_sites(board=SUMMER_CAMP, phase="notice", db=db)
        from crawler.ops_health import record_forum_crawl
        if not items:
            record_forum_crawl(found=0, new=0)
            return 0, 0
        from crawler.fetcher import create_http_client

        async with create_http_client() as client:
            items = await enrich_baoyan_items(
                items, client=client, board=SUMMER_CAMP, phase="notice",
            )
        if not items:
            return 0, 0
        new, _ = save_announcements(db, items)
        sync_coverage_from_announcements(db)
        db.commit()

        from crawler.llm_enrich_state import record_failure
        from models import Announcement

        for item in items:
            if not getattr(item, "forum_incomplete", False):
                continue
            ann = db.query(Announcement).filter(Announcement.url == item.url).first()
            if ann:
                record_failure(ann.id, "forum_needs_link")

        logger.info("保研网站入库：发现 %d，新增 %d", len(items), new)
        from crawler.ops_health import record_forum_crawl
        record_forum_crawl(found=len(items), new=new)
        return len(items), new
    finally:
        if own_db and db:
            db.close()


async def run_wechat_daily(*, force_sogou: bool = False) -> dict:
    """每日聚合通道：保研网站 + RSS；搜狗默认跳过。"""
    baoyan_found, baoyan_new = await run_baoyan_sites_crawl()
    rss_found, rss_new = await run_wechat_rss_crawl()
    sogou_found, sogou_new = 0, 0
    if settings.wechat_sogou_enabled or force_sogou:
        sogou_found, sogou_new = await run_wechat_sogou_scan(force=force_sogou)
    else:
        logger.info("搜狗微信已停用，跳过扫描")
    return {
        "baoyan_found": baoyan_found,
        "baoyan_new": baoyan_new,
        "rss_found": rss_found,
        "rss_new": rss_new,
        "sogou_found": sogou_found,
        "sogou_new": sogou_new,
    }
