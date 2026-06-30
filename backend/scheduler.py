import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from crawler.boards import PRE_ADMISSION, SUMMER_CAMP
from crawler.service import run_crawl
from crawler.wechat_jobs import run_wechat_daily, run_wechat_rss_crawl, run_baoyan_sites_crawl

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _wechat_daily_job():
    try:
        result = await run_wechat_daily()
        logger.info("微信定时任务完成: %s", result)
    except Exception as e:
        logger.warning("微信定时任务失败: %s", e)


async def _baoyan_sites_job():
    try:
        found, new = await run_baoyan_sites_crawl()
        logger.info("保研网站定时: 发现 %d 新增 %d", found, new)
    except Exception as e:
        logger.warning("保研网站抓取失败: %s", e)


async def _wechat_rss_job():
    try:
        found, new = await run_wechat_rss_crawl()
        logger.info("微信 RSS 定时: 发现 %d 新增 %d", found, new)
    except Exception as e:
        logger.warning("微信 RSS 失败: %s", e)


def start_scheduler():
    if not settings.scheduler_enabled:
        logger.info("定时检索已关闭（scheduler_enabled=false），打开页面直接读数据库")
        return

    scheduler.add_job(
        run_crawl,
        "interval",
        minutes=settings.crawl_interval_minutes,
        id="crawl_summer_camp",
        replace_existing=True,
        max_instances=1,
        kwargs={"board": SUMMER_CAMP},
    )
    scheduler.add_job(
        run_crawl,
        "interval",
        minutes=settings.crawl_interval_minutes,
        id="crawl_pre_admission",
        replace_existing=True,
        max_instances=1,
        kwargs={"board": PRE_ADMISSION},
    )

    if settings.baoyan_sites_enabled:
        scheduler.add_job(
            _baoyan_sites_job,
            "interval",
            hours=settings.baoyan_sites_interval_hours,
            id="baoyan_sites",
            replace_existing=True,
            max_instances=1,
        )

    if settings.wechat_rss_enabled:
        scheduler.add_job(
            _wechat_rss_job,
            "interval",
            hours=settings.wechat_rss_interval_hours,
            id="wechat_rss",
            replace_existing=True,
            max_instances=1,
        )

    if settings.wechat_sogou_enabled:
        scheduler.add_job(
            _wechat_daily_job,
            "cron",
            hour=9,
            minute=0,
            id="wechat_sogou_daily",
            replace_existing=True,
            max_instances=1,
        )

    scheduler.start()
    logger.info("Scheduler started — each board every %d min", settings.crawl_interval_minutes)


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
