import asyncio
import logging
from typing import Callable
from urllib.parse import quote

import httpx

from config import settings
from crawler.boards import SUMMER_CAMP, is_relevant_for_crawl, search_keywords
from crawler.grad_school_domains import get_search_domains
from crawler.official_verify import resolve_final_url, verify_official_url
from crawler.listing_resolver import is_listing_url
from crawler.parser import ParsedAnnouncement, is_valid_announcement_url, parse_search_results

logger = logging.getLogger(__name__)


def _search_domains(target) -> list[str]:
    """检索用域名列表：学院子域 → 学校根域 → 研究生院。"""
    return get_search_domains(target)


def build_search_queries(target, board: str, phase: str) -> list[str]:
    """为单个学院目标生成全网检索关键词（site:域名 + 学校名，避免与城市名混淆）。"""
    keywords = search_keywords(board, phase, target.college_type)[:settings.search_max_per_target]
    domains = _search_domains(target)[: settings.search_max_domains]
    queries: list[str] = []
    for kw in keywords:
        if domains:
            for domain in domains:
                queries.append(
                    f"site:{domain} {target.university} {target.college} {kw}"
                )
        else:
            queries.append(
                f"site:edu.cn {target.university} {target.college} {kw}"
            )
    return queries


async def _search_engine(query: str, college_type: str, board: str, phase: str) -> list[ParsedAnnouncement]:
    if settings.search_engine == "baidu":
        return await search_baidu(query, college_type, board, phase)
    return await search_bing(query, college_type, board, phase)


BING_SEARCH_URL = "https://cn.bing.com/search?q={query}&count=20"


async def search_bing(
    query: str,
    college_type: str,
    board: str | None = None,
    phase: str | None = None,
) -> list[ParsedAnnouncement]:
    """通过 Bing 全网检索夏令营/预推免通知。"""
    url = BING_SEARCH_URL.format(query=quote(query))
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers=headers,
            verify=False,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("Bing search failed: %s status=%d", query, resp.status_code)
                return []
            return parse_search_results(
                resp.text, college_type, board=board, phase=phase,
            )
    except Exception as e:
        logger.warning("Bing search error for '%s': %s", query, e)
        return []


async def search_baidu(
    query: str,
    college_type: str,
    board: str | None = None,
    phase: str | None = None,
) -> list[ParsedAnnouncement]:
    """通过百度检索，失败时回退 Bing。"""
    url = f"https://www.baidu.com/s?wd={quote(query)}&rn=20"
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers=headers,
            verify=False,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200 or "captcha" in str(resp.url):
                return await search_bing(query, college_type, board, phase)
            results = parse_search_results(
                resp.text, college_type, board=board, phase=phase,
            )
            if not results:
                return await search_bing(query, college_type, board, phase)
            return results
    except Exception as e:
        logger.warning("Baidu search error for '%s': %s", query, e)
        return await search_bing(query, college_type, board, phase)


async def verify_search_item(
    item: ParsedAnnouncement,
    target,
    client: httpx.AsyncClient,
    *,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
) -> ParsedAnnouncement | None:
    """解析最终 URL 并校验是否为官方权威来源。"""
    final_url = await resolve_final_url(item.url, client)
    if not is_valid_announcement_url(final_url):
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

    from crawler.detail_enricher import enrich_announcement, infer_board_from_item
    from crawler.url_quality import assess_notice_url, is_recap_notice, is_stale_url

    board_guess = board or infer_board_from_item(item) or SUMMER_CAMP
    phase_guess = phase or "notice"
    try:
        await enrich_announcement(
            item, board=board_guess, phase=phase_guess, client=client,
        )
    except Exception as e:
        logger.debug("Detail enrich failed %s: %s", final_url, e)

    if is_recap_notice(item.title) or is_stale_url(item.url):
        logger.info("跳过非招生通知: %s", item.url)
        return None

    level, reason = assess_notice_url(item)
    if level == "bad" and not item.deadline:
        logger.info("跳过不可信 URL (%s): %s", reason, item.url)
        return None

    if is_listing_url(item.url) and not item.deadline:
        logger.info("跳过无法下钻的汇总页: %s", item.url)
        return None

    return item


async def search_one_target(
    target,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> list[ParsedAnnouncement]:
    """对单个学院执行全网检索并校验。"""
    keywords = search_keywords(board, phase, target.college_type)[:settings.search_max_per_target]
    domains = _search_domains(target)[: settings.search_max_domains]
    raw_items: list[ParsedAnnouncement] = []

    for kw in keywords:
        domain_list = domains if domains else [None]
        found_for_kw = False
        for domain in domain_list:
            if domain:
                query = f"site:{domain} {target.university} {target.college} {kw}"
            else:
                query = f"site:edu.cn {target.university} {target.college} {kw}"
            async with sem:
                await asyncio.sleep(settings.search_request_delay)
                batch = await _search_engine(
                    query, target.college_type, board, phase,
                )
            raw_items.extend(batch)
            if batch:
                found_for_kw = True
                if settings.search_compact_keywords:
                    break
        if found_for_kw and settings.search_compact_keywords:
            break

    verified: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    for item in raw_items:
        if not is_relevant_for_crawl(item.title, board, phase):
            continue
        v = await verify_search_item(item, target, client, board=board, phase=phase)
        if not v or v.url in seen:
            continue
        seen.add(v.url)
        verified.append(v)
    return verified


async def search_for_targets(
    targets: list,
    board: str,
    phase: str,
    *,
    on_batch: Callable[[list], None] | None = None,
    on_target_complete: Callable[[object, list], None] | None = None,
    batch_size: int | None = None,
) -> list[ParsedAnnouncement]:
    """对学院目标列表执行全网关键词检索，校验后返回权威来源通知。"""
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

    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml",
    }

    async with httpx.AsyncClient(
        timeout=settings.request_timeout,
        follow_redirects=True,
        headers=headers,
        verify=False,
    ) as client:

        async def run_one(target):
            try:
                verified = await search_one_target(target, board, phase, client, sem)
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
    """兼容旧接口：按学院类型批量检索（测试/诊断用）。"""
    from crawler.university_config import UNIVERSITY_TARGETS

    targets = [t for t in UNIVERSITY_TARGETS if t.college_type == college_type]
    items = await search_for_targets(targets, "summer_camp", "notice")
    return items
