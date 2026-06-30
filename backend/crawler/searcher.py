import asyncio
import logging
from typing import Callable
from urllib.parse import quote

import httpx

from config import settings
from crawler.fetcher import create_http_client
from crawler.boards import SUMMER_CAMP, is_relevant_for_crawl, search_keywords
from crawler.grad_school_domains import get_search_domains
from crawler.official_verify import resolve_final_url, verify_official_url
from crawler.listing_resolver import is_listing_url
from crawler.parser import ParsedAnnouncement, is_valid_announcement_url, parse_search_results
from crawler.listing_resolver import is_notice_page_url
from crawler.url_quality import assess_notice_url, is_recap_notice, is_stale_url, score_search_hit
from crawler.parser import core_times_complete
from crawler.search_exclusions import append_search_exclusions

logger = logging.getLogger(__name__)


def _search_domains(target) -> list[str]:
    return get_search_domains(target)


def build_search_queries(target, board: str, phase: str) -> list[str]:
    """生成检索词；快速模式：1 关键词 × 最多 2 个域名（学院子域 + 学校根域）。"""
    from urllib.parse import urlparse
    from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS

    keywords = search_keywords(board, phase, target.college_type)
    if not keywords:
        return []

    limit_kw = 1 if settings.crawl_fast_mode else settings.search_max_per_target
    limit_dom = 2 if settings.crawl_fast_mode else settings.search_max_domains
    keywords = keywords[:limit_kw]
    year = settings.min_notice_year

    raw_domains = _search_domains(target)
    ordered: list[str] = []
    seen_dom: set[str] = set()
    if target.base_url:
        host = urlparse(target.base_url).netloc.lower().replace("www.", "")
        if host:
            ordered.append(host)
            seen_dom.add(host)
    root = UNIVERSITY_ROOT_DOMAINS.get(target.university, "")
    if root and root not in seen_dom:
        ordered.append(root)
        seen_dom.add(root)
    for d in raw_domains:
        if d not in seen_dom:
            ordered.append(d)
            seen_dom.add(d)
    domains = ordered[:limit_dom]

    queries: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        domain_list = domains if domains else [None]
        for domain in domain_list:
            if domain:
                q = f"site:{domain} {target.university} {target.college} {kw} {year}"
            else:
                q = f"site:edu.cn {target.university} {target.college} {kw} {year}"
            q = append_search_exclusions(q)
            if q not in seen:
                seen.add(q)
                queries.append(q)
    return queries


async def _search_engine(
    query: str,
    college_type: str,
    board: str,
    phase: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[ParsedAnnouncement]:
    if settings.search_engine == "baidu":
        return await search_baidu(query, college_type, board, phase, client=client)
    return await search_bing(query, college_type, board, phase, client=client)


BING_SEARCH_URL = "https://cn.bing.com/search?q={query}&count=20"


async def search_bing(
    query: str,
    college_type: str,
    board: str | None = None,
    phase: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[ParsedAnnouncement]:
    url = BING_SEARCH_URL.format(query=quote(query))

    async def _do(c: httpx.AsyncClient) -> list[ParsedAnnouncement]:
        resp = await c.get(url)
        if resp.status_code != 200:
            logger.warning("Bing search failed: %s status=%d", query, resp.status_code)
            return []
        return parse_search_results(
            resp.text, college_type, board=board, phase=phase,
        )

    try:
        if client is not None:
            return await _do(client)
        async with create_http_client() as c:
            return await _do(c)
    except Exception as e:
        logger.warning("Bing search error for '%s': %s", query, e)
        return []


async def search_baidu(
    query: str,
    college_type: str,
    board: str | None = None,
    phase: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[ParsedAnnouncement]:
    url = f"https://www.baidu.com/s?wd={quote(query)}&rn=20"

    async def _do(c: httpx.AsyncClient) -> list[ParsedAnnouncement]:
        resp = await c.get(url)
        if resp.status_code != 200 or "captcha" in str(resp.url):
            return await search_bing(query, college_type, board, phase, client=c)
        results = parse_search_results(
            resp.text, college_type, board=board, phase=phase,
        )
        if not results:
            return await search_bing(query, college_type, board, phase, client=c)
        return results

    try:
        if client is not None:
            return await _do(client)
        async with create_http_client() as c:
            return await _do(c)
    except Exception as e:
        logger.warning("Baidu search error for '%s': %s", query, e)
        return await search_bing(query, college_type, board, phase, client=client)


def _snippet_complete(item: ParsedAnnouncement) -> bool:
    from crawler.parser import core_times_complete
    return core_times_complete(item) and item.event_format


async def verify_search_item(
    item: ParsedAnnouncement,
    target,
    client: httpx.AsyncClient,
    *,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
) -> ParsedAnnouncement | None:
    final_url = await resolve_final_url(item.url, client)
    if not is_valid_announcement_url(final_url):
        return None

    from crawler.noise_filter import passes_title_filter

    if not passes_title_filter(item.title or "", item.summary or "", board=board, phase=phase, url=final_url):
        return None

    ok, source_label = verify_official_url(
        final_url, item.title, target, item.summary,
    )
    if not ok:
        return None

    item.url = final_url
    item.source = source_label
    item.university = target.university
    item.college = target.college
    item.college_type = target.college_type

    skip_enrich = _snippet_complete(item)
    if not skip_enrich:
        from crawler.detail_enricher import enrich_announcement, infer_board_from_item

        board_guess = board or infer_board_from_item(item) or SUMMER_CAMP
        phase_guess = phase or "notice"
        try:
            await enrich_announcement(
                item,
                board=board_guess,
                phase=phase_guess,
                client=client,
                mode="light" if settings.crawl_fast_mode else "crawl",
            )
        except Exception as e:
            logger.debug("Detail enrich failed %s: %s", final_url, e)

    if is_recap_notice(item.title) or is_stale_url(item.url):
        return None

    level, reason = assess_notice_url(item)
    if level == "bad" and not item.deadline:
        if is_notice_page_url(item.url) and any(
            k in (item.title or "") for k in ("夏令营", "预推免", "推免")
        ):
            pass
        else:
            logger.debug("跳过不可信 URL (%s): %s", reason, item.url)
            return None

    if is_listing_url(item.url) and not item.deadline:
        logger.debug("跳过无法下钻的汇总页: %s", item.url)
        return None

    return item


async def search_one_target(
    target,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> list[ParsedAnnouncement]:
    queries = build_search_queries(target, board, phase)
    raw_items: list[ParsedAnnouncement] = []
    seen_urls: set[str] = set()
    delay = 0.0 if settings.crawl_fast_mode else settings.search_request_delay

    for query in queries:
        async with sem:
            if delay > 0:
                await asyncio.sleep(delay)
            batch = await _search_engine(
                query, target.college_type, board, phase, client=client,
            )
        for item in batch:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                raw_items.append(item)
        if batch and settings.search_compact_keywords:
            break

    if not raw_items and settings.crawl_fast_mode:
        fallback = f"site:edu.cn {target.university} {target.college} 夏令营 {settings.min_notice_year}"
        async with sem:
            batch = await _search_engine(
                fallback, target.college_type, board, phase, client=client,
            )
        for item in batch:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                raw_items.append(item)

    if not raw_items:
        return []

    ranked = sorted(
        raw_items,
        key=lambda x: score_search_hit(x, target),
        reverse=True,
    )
    candidates = [
        x for x in ranked
        if is_relevant_for_crawl(x.title, board, phase)
    ][: settings.search_verify_max_results]

    if not candidates:
        return []

    if settings.crawl_fast_mode and len(candidates) > 1:
        tasks = [
            verify_search_item(item, target, client, board=board, phase=phase)
            for item in candidates
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        verified = [
            r for r in results
            if isinstance(r, ParsedAnnouncement) and r is not None
        ]
        if verified:
            with_deadline = [v for v in verified if v.deadline]
            return with_deadline[:1] if with_deadline else verified[:1]

    verified: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    for item in candidates:
        v = await verify_search_item(
            item, target, client,
            board=board, phase=phase,
        )
        if not v or v.url in seen:
            continue
        seen.add(v.url)
        verified.append(v)
        if v.deadline and core_times_complete(v):
            break
    return verified


async def _search_one_target_timed(
    target,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> list[ParsedAnnouncement]:
    timeout = settings.search_target_timeout_sec
    try:
        return await asyncio.wait_for(
            search_one_target(target, board, phase, client, sem),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "学院检索超时 (%ds): %s %s",
            timeout, target.university, target.college,
        )
        return []


async def search_for_targets(
    targets: list,
    board: str,
    phase: str,
    *,
    on_batch: Callable[[list], None] | None = None,
    on_target_complete: Callable[[object, list], None] | None = None,
    batch_size: int | None = None,
) -> list[ParsedAnnouncement]:
    if not targets:
        return []

    batch_size = batch_size or settings.search_save_batch_size
    sem = asyncio.Semaphore(settings.search_max_concurrent)
    merged: list[ParsedAnnouncement] = []
    buffer: list[ParsedAnnouncement] = []
    buffer_lock = asyncio.Lock()

    async def emit(items: list[ParsedAnnouncement]) -> None:
        if not items:
            return
        async with buffer_lock:
            buffer.extend(items)
            while on_batch and len(buffer) >= batch_size:
                chunk = buffer[:batch_size]
                del buffer[:batch_size]
                on_batch(chunk)

    async with create_http_client() as client:

        async def run_one(target):
            try:
                verified = await _search_one_target_timed(
                    target, board, phase, client, sem,
                )
            except Exception as e:
                logger.warning("Search target error %s: %s", target.university, e)
                verified = []
            if on_target_complete:
                on_target_complete(target, verified)
            elif verified:
                await emit(verified)
            return verified

        tasks = [asyncio.create_task(run_one(t)) for t in targets]
        for fut in asyncio.as_completed(tasks):
            try:
                result = await fut
            except Exception as e:
                logger.warning("Search task error: %s", e)
                continue
            merged.extend(result)

    if on_batch and buffer:
        on_batch(buffer)
        buffer.clear()

    logger.info(
        "%s-%s 全网检索：%d 个学院，命中 %d 条权威通知",
        board, phase, len(targets), len(merged),
    )
    return merged


async def search_all(college_type: str) -> list[ParsedAnnouncement]:
    from crawler.university_config import UNIVERSITY_TARGETS

    targets = [t for t in UNIVERSITY_TARGETS if t.college_type == college_type]
    return await search_for_targets(targets, "summer_camp", "notice")
