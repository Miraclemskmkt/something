"""多源发现：列表探测优先 → 轻量泛搜 → 聚焦爬虫（带预算/熔断）。"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from config import settings
from crawler.broad_search import build_broad_queries
from crawler.boards import is_relevant_for_crawl
from crawler.focus_crawler import focus_crawl_college
from crawler.notice_list_probe import discover_notice_list_urls, quick_reachable
from crawler.parser import ParsedAnnouncement, core_times_complete, parse_news_list
from crawler.request_budget import CollegeBudget
from crawler.request_cache import (
    get_list_page,
    get_list_seeds,
    get_search_cache,
    save_list_seeds,
    set_list_page,
    set_search_cache,
)
from crawler.searcher import verify_search_item, _search_engine
from crawler.source_labels import OFFICIAL_LABEL
from crawler.url_quality import score_search_hit
from crawler.detail_enricher import enrich_announcement

logger = logging.getLogger(__name__)


def _make_budget() -> CollegeBudget:
    return CollegeBudget(
        max_requests=settings.college_max_requests,
        total_sec=float(settings.college_total_timeout_sec),
        search_budget_sec=float(settings.college_search_budget_sec),
    )


async def _search_with_timeout(
    query: str,
    target,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
) -> list[ParsedAnnouncement]:
    cached = get_search_cache(query)
    if cached is not None:
        return cached

    try:
        batch = await asyncio.wait_for(
            _search_engine(query, target.college_type, board, phase, client=client),
            timeout=settings.search_query_timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.debug("搜索超时 %ds: %s", settings.search_query_timeout_sec, query[:60])
        return []
    except Exception as e:
        logger.debug("搜索失败: %s", e)
        return []

    set_search_cache(query, batch, settings.search_cache_ttl_sec)
    return batch


async def _run_broad_search(
    target,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    budget: CollegeBudget,
) -> list[ParsedAnnouncement]:
    if budget.search_expired() or not budget.can_request():
        return []

    budget.begin_search()
    queries = build_broad_queries(target, board, phase)[: settings.broad_search_max_queries]
    if not queries:
        return []

    tasks: list[asyncio.Task] = []
    for q in queries:
        if budget.search_expired() or not budget.can_request():
            break
        budget.consume()
        tasks.append(asyncio.create_task(_search_with_timeout(q, target, board, phase, client)))

    if not tasks:
        return []

    batches = await asyncio.gather(*tasks, return_exceptions=True)
    raw: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    for batch in batches:
        if isinstance(batch, Exception):
            continue
        for item in batch:
            if item.url not in seen:
                seen.add(item.url)
                raw.append(item)

    if not raw:
        return []

    candidates = [
        x for x in sorted(raw, key=lambda x: score_search_hit(x, target), reverse=True)
        if is_relevant_for_crawl(x.title, board, phase)
    ][: settings.search_verify_max_results]

    verified: list[ParsedAnnouncement] = []
    verify_timeout = settings.enrich_timeout_light_sec + 2
    for item in candidates:
        if budget.expired():
            break
        try:
            v = await asyncio.wait_for(
                verify_search_item(item, target, client, board=board, phase=phase),
                timeout=verify_timeout,
            )
        except asyncio.TimeoutError:
            continue
        if v:
            verified.append(v)
            if core_times_complete(v):
                break
    return verified


async def _fetch_list_html(
    url: str,
    client: httpx.AsyncClient,
    budget: CollegeBudget,
) -> str | None:
    if not budget.can_request():
        return None
    cached = get_list_page(url)
    if cached:
        return cached
    budget.consume()
    try:
        resp = await client.get(
            url,
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout,
                read=min(settings.http_read_timeout, 2.5),
                write=min(settings.http_read_timeout, 2.5),
                pool=settings.http_connect_timeout,
            ),
        )
        if resp.status_code != 200:
            return None
        html = resp.text or ""
        if len(html) >= 200:
            set_list_page(url, html, settings.list_page_cache_ttl_sec)
            return html
    except Exception:
        pass
    return None


async def _crawl_list_urls(
    target,
    list_urls: list[str],
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    budget: CollegeBudget,
) -> list[ParsedAnnouncement]:
    results: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    base = target.base_url or (list_urls[0] if list_urls else "")
    max_lists = min(settings.notice_probe_parallel_paths, 2)
    max_items = settings.official_list_max_items
    max_detail = settings.college_max_detail_fetches
    candidates = list(dict.fromkeys(list_urls))[:max_lists]

    async def _try_one(url: str) -> tuple[str, str | None]:
        return url, await _fetch_list_html(url, client, budget)

    pages = await asyncio.gather(*[_try_one(u) for u in candidates])
    for url, html in pages:
        if budget.expired() or not html:
            continue
        items = parse_news_list(html, base, target.college_type, board=board, phase=phase)
        picked = 0
        for item in items[:max_items + 5]:
            if item.url in seen:
                continue
            if not is_relevant_for_crawl(item.title, board, phase):
                continue
            item.university = target.university
            item.college = target.college
            item.college_type = target.college_type
            item.source = OFFICIAL_LABEL
            seen.add(item.url)
            if picked < max_detail and budget.can_request():
                try:
                    await asyncio.wait_for(
                        enrich_announcement(
                            item, board=board, phase=phase, client=client, mode="light",
                        ),
                        timeout=settings.enrich_timeout_light_sec,
                    )
                except Exception:
                    pass
                picked += 1
            results.append(item)
            if len(results) >= max_items:
                return results
        if results:
            return results
    return results[:max_items]


async def discover_for_target(
    target,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    search_sem: asyncio.Semaphore,
    crawl_sem: asyncio.Semaphore,
) -> list[ParsedAnnouncement]:
    """单学院多源发现（总超时 college_total_timeout_sec）。"""
    budget = _make_budget()
    t0 = time.monotonic()

    async def _run() -> list[ParsedAnnouncement]:
        if not target.base_url:
            return await _run_broad_search(target, board, phase, client, budget)

        # 0. 持久化种子 URL
        seeds = get_list_seeds(target.university, target.college, target.college_type)
        seed_urls = list(dict.fromkeys(seeds + list(target.news_urls or [])))[:2]

        # 1. 列表页优先（HEAD 探测 + 第一页）
        if settings.notice_list_probe_enabled and budget.can_request():
            async with crawl_sem:
                if seed_urls or await quick_reachable(target.base_url, client, budget):
                    list_urls = seed_urls
                    if not list_urls:
                        list_urls = await discover_notice_list_urls(
                            target.base_url, client, budget=budget,
                        )
                    if list_urls:
                        save_list_seeds(
                            target.university, target.college, target.college_type, list_urls,
                        )
                        items = await _crawl_list_urls(
                            target, list_urls, board, phase, client, budget,
                        )
                        if items:
                            return items

        # 2. 泛搜（预算内，最多 2 词并行 × 5s）
        if (
            settings.search_strategy == "broad"
            and not budget.search_expired()
            and (time.monotonic() - t0) < settings.college_fast_target_sec
        ):
            async with search_sem:
                items = await _run_broad_search(target, board, phase, client, budget)
            if items:
                return items

        # 3. 轻量聚焦爬虫（深度 2，≤5 请求；剩余时间不足则跳过）
        elapsed = time.monotonic() - t0
        focus_cap = min(
            settings.focus_crawler_max_sec,
            budget.remaining_sec(),
            max(2.0, settings.college_fast_target_sec - elapsed),
        )
        if (
            settings.focus_crawler_enabled
            and budget.can_request()
            and focus_cap >= 2.0
            and elapsed < settings.college_total_timeout_sec
        ):
            try:
                async with crawl_sem:
                    items = await focus_crawl_college(
                        target, board, phase, client, budget=budget, max_sec=focus_cap,
                    )
                if items:
                    return items
            except asyncio.TimeoutError:
                pass

        return []

    try:
        items = await asyncio.wait_for(
            _run(),
            timeout=max(1.0, budget.remaining_sec()),
        )
    except asyncio.TimeoutError:
        logger.warning(
            "学院总超时 (%ds): %s %s",
            settings.college_total_timeout_sec,
            target.university,
            target.college,
        )
        items = []

    elapsed = time.monotonic() - t0
    if elapsed > settings.college_slow_threshold_sec:
        logger.info(
            "慢学院 %.1fs %s %s (req=%d)",
            elapsed, target.university, target.college, budget.used_requests,
        )
    return items
