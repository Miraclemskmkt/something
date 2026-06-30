"""待定学院补抓：仅聚焦爬虫 / 研招网，不做搜索引擎。"""
from __future__ import annotations

import asyncio
import logging

import httpx

from config import settings
from crawler.boards import SUMMER_CAMP
from crawler.focus_crawler import focus_crawl_college
from crawler.grad_portal_probe import discover_from_grad_portals
from crawler.multi_source import _crawl_list_urls
from crawler.notice_list_probe import discover_notice_list_urls, quick_reachable
from crawler.parser import ParsedAnnouncement
from crawler.request_budget import CollegeBudget
from crawler.request_cache import get_list_seeds, save_list_seeds
from official_sites import derive_news_urls

logger = logging.getLogger(__name__)


def refresh_targets():
    """域名覆盖写入后刷新内存中的目标列表。"""
    import college_registry
    import crawler.university_config as uc

    college_registry.REGISTRY_ENTRIES = college_registry.build_registry_entries()
    uc.UNIVERSITY_TARGETS = [uc._to_target(e) for e in college_registry.REGISTRY_ENTRIES]


def find_target(university: str, college: str, college_type: str):
    from crawler.university_config import UNIVERSITY_TARGETS

    for t in UNIVERSITY_TARGETS:
        if t.university == university and t.college == college and t.college_type == college_type:
            return t
    return None


async def recrawl_one(
    target,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    *,
    focus_sec: float = 5.0,
    focus_depth: int = 3,
) -> list[ParsedAnnouncement]:
    budget = CollegeBudget(
        max_requests=10,
        total_sec=focus_sec + 6.0,
        search_budget_sec=0,
    )
    base = target.base_url
    if not base:
        return []

    seeds = get_list_seeds(target.university, target.college, target.college_type)
    seed_urls = list(dict.fromkeys(seeds + list(target.news_urls or [])))[:3]

    if seed_urls or await quick_reachable(base, client, budget):
        list_urls = seed_urls
        if not list_urls:
            list_urls = await discover_notice_list_urls(base, client, budget=budget)
        if list_urls:
            save_list_seeds(target.university, target.college, target.college_type, list_urls)
            items = await _crawl_list_urls(target, list_urls, board, phase, client, budget)
            if items:
                return items

    items = await focus_crawl_college(
        target, board, phase, client, budget=budget,
        max_sec=focus_sec, max_depth=focus_depth,
    )
    if items:
        return items

    return await discover_from_grad_portals(target, board, phase, client)


async def batch_recrawl(
    targets: list,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
    *,
    concurrency: int = 16,
) -> dict[tuple[str, str, str], list[ParsedAnnouncement]]:
    from crawler.fetcher import create_http_client

    sem = asyncio.Semaphore(concurrency)
    out: dict[tuple[str, str, str], list[ParsedAnnouncement]] = {}

    async with create_http_client() as client:
        async def one(t):
            async with sem:
                key = (t.university, t.college, t.college_type)
                try:
                    items = await recrawl_one(t, board, phase, client)
                    if items:
                        out[key] = items
                except Exception as e:
                    logger.debug("补抓失败 %s %s: %s", t.university, t.college, e)

        await asyncio.gather(*[one(t) for t in targets])
    return out
