import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from crawler.boards import PRE_ADMISSION, SUMMER_CAMP
from crawler.service import run_crawl

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


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
    scheduler.start()
    logger.info("Scheduler started — each board every %d min", settings.crawl_interval_minutes)


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
