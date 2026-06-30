"""待定学院探测与分类。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from config import settings
from crawler.notice_calendar import in_activation_window, next_check_datetime
from crawler.notice_list_probe import quick_reachable
from crawler.pending_kinds import DOMAIN_FAILURE, NOT_PUBLISHED, WATCHING
from crawler.request_budget import CollegeBudget

logger = logging.getLogger(__name__)


async def probe_domain_status(base_url: str, client: httpx.AsyncClient) -> str:
    """reachable | unreachable"""
    if not base_url:
        return "unreachable"
    budget = CollegeBudget(max_requests=2, total_sec=8, search_budget_sec=0)
    if await quick_reachable(base_url, client, budget):
        return "reachable"
    return "unreachable"


async def classify_college(
    target,
    board: str,
    client: httpx.AsyncClient,
) -> tuple[str, str, datetime]:
    """
    返回 (pending_kind, domain_status, next_check_at)。
    - 域名不可达 → domain_failure，7 天后可再试域名修正
    - 域名可达、无通知 → not_published，按日历设定 next_check_at
    """
    status = await probe_domain_status(target.base_url, client)
    now = datetime.now()

    if status == "unreachable":
        return DOMAIN_FAILURE, status, now + __import__("datetime").timedelta(days=7)

    nxt = next_check_datetime(target.university, target.college_type, board)
    return NOT_PUBLISHED, status, nxt


def should_crawl_target(
    pending_kind: str | None,
    next_check_at: datetime | None,
    university: str,
    college_type: str,
    board: str,
) -> bool:
    """是否允许本轮发起爬虫请求。"""
    if not pending_kind:
        return True
    if pending_kind == DOMAIN_FAILURE:
        return False
    if pending_kind == WATCHING:
        if next_check_at and datetime.now() < next_check_at:
            return False
        return in_activation_window(university, college_type, board)
    if pending_kind == NOT_PUBLISHED:
        if next_check_at and datetime.now() < next_check_at:
            return False
        return in_activation_window(university, college_type, board)
    return True
