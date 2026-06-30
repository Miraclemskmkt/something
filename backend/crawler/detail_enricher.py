"""通知详情补全：详情页抓取 → 列表页下钻 → Playwright → Bing 检索兜底。"""
import asyncio
import logging

import httpx

from config import settings
from crawler.boards import PRE_ADMISSION, SUMMER_CAMP
from crawler.fetcher import fetch_page, is_blocked_html
from crawler.grad_school_domains import bing_site_queries
from crawler.listing_resolver import is_listing_page, is_listing_url, pick_detail_from_listing
from crawler.url_quality import (
    assess_notice_url,
    is_aggregate_notice,
    is_pdf_url,
    is_recap_notice,
    is_stale_url,
    score_notice_candidate,
    should_reresolve_url,
)
from crawler.attachment_extractor import fetch_attachment_text, is_attachment_url
from crawler.parser import (
    ParsedAnnouncement,
    core_times_complete,
    enrich_from_html,
    extract_date_from_url,
    is_valid_announcement_url,
    merge_times_into_item,
)

logger = logging.getLogger(__name__)

EnrichMode = str  # "light" | "crawl" | "normal" | "full"


def _apply_content_enrich(
    item: ParsedAnnouncement,
    html: str,
    url: str,
) -> bool:
    """正文抓取后：LLM 分类 → LLM 主力提取 / 正则兜底。返回 False 表示 LLM 拒收。"""
    from config import settings
    from crawler.parser import extract_page_text, extract_table_text

    text = extract_page_text(html, title=item.title or "")
    if not text and not html.strip().startswith("%PDF"):
        enrich_from_html(item, html)
        return True

    if not text:
        text = html[:8000]

    table = extract_table_text(html) if "<table" in html.lower() else ""

    if not item.summary or len(item.summary or "") < 80:
        item.summary = text[:500]

    if settings.llm_enabled and settings.llm_classify_enabled:
        from crawler.llm_classifier import classify_notice_relevance

        clf = classify_notice_relevance(
            item.title or "",
            text,
            college_type=item.college_type or "law",
            url=url,
        )
        if not clf.relevant and clf.failure_type == "success":
            item.llm_rejected = True
            return False

    if settings.llm_enabled and settings.llm_extract_first:
        from crawler.llm_extractor import merge_llm_then_regex

        merge_llm_then_regex(
            item, text, url=url, table_text=table, html=html,
        )
    else:
        enrich_from_html(item, html)

    return True


def _needs_enrich(item: ParsedAnnouncement) -> bool:
    return not core_times_complete(item) or not item.event_format


def _apply_snippet(item: ParsedAnnouncement, title: str, snippet: str) -> bool:
    from crawler.parser import compact_spaced_text

    focused = compact_spaced_text(f"{title} {snippet}")
    before = (item.publish_date, item.deadline, item.event_time, item.event_format)
    merge_times_into_item(item, focused)
    after = (item.publish_date, item.deadline, item.event_time, item.event_format)
    if snippet and (not item.summary or len(item.summary or "") < 40):
        item.summary = snippet[:500]
    return before != after


def _remember_url_change(item: ParsedAnnouncement, original: str) -> None:
    if item.url and original and item.url != original:
        item.original_url = original


def _bing_query_limit(mode: EnrichMode) -> int:
    base = settings.bing_fallback_max_queries
    if mode in ("light", "crawl"):
        return min(base, 2) if mode == "crawl" else 0
    if mode == "full":
        return max(base, base + 2)
    return base


def _compact_bing_queries(
    item: ParsedAnnouncement,
    board: str,
    *,
    limit: int,
) -> list[str]:
    """精简 Bing 查询，避免单条补全卡死。"""
    from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS
    from crawler.search_exclusions import append_search_exclusions

    if limit <= 0:
        return []

    uni = item.university or ""
    col = item.college or ""
    root = UNIVERSITY_ROOT_DOMAINS.get(uni, "")
    short_title = (item.title or "")[:32].strip()

    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = append_search_exclusions(q.strip())
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    if board == PRE_ADMISSION:
        add(f"{uni} {col} 预推免 2026 site:edu.cn")
    else:
        add(f"{uni} {col} 夏令营 招生通知 2026 site:edu.cn")
        add(f"{uni} {col} 夏令营 报名截止 2026 site:edu.cn")

    if short_title:
        add(f"{short_title} {uni} site:edu.cn")

    if root:
        add(f"site:{root} {col} 夏令营 2026")
        for host in bing_site_queries(uni, col, "夏令营", root)[:2]:
            add(host)

    return queries[:limit]


async def _bing_candidates(
    item: ParsedAnnouncement,
    board: str,
    phase: str,
    *,
    client: httpx.AsyncClient | None = None,
    mode: EnrichMode = "normal",
) -> list[ParsedAnnouncement]:
    from crawler.searcher import search_bing

    college_type = item.college_type or "law"
    queries = _compact_bing_queries(item, board, limit=_bing_query_limit(mode))
    if not queries:
        return []

    seen_url: set[str] = set()
    merged: list[ParsedAnnouncement] = []
    delay = settings.search_request_delay

    for i, q in enumerate(queries):
        if i > 0 and delay > 0:
            await asyncio.sleep(delay)
        try:
            batch = await search_bing(q, college_type, board, phase, client=client)
        except Exception as e:
            logger.debug("Bing fallback query failed: %s", e)
            continue
        for r in batch:
            if r.url in seen_url:
                continue
            seen_url.add(r.url)
            merged.append(r)
        if merged and any(r.deadline for r in merged):
            break
    return merged


async def _crawl_college_news(item: ParsedAnnouncement, board: str, phase: str) -> list[ParsedAnnouncement]:
    """从学院官网通知栏找更匹配的详情链接。"""
    from crawler.parser import parse_news_list
    from crawler.university_config import UNIVERSITY_TARGETS
    from official_sites import derive_news_urls

    target = next(
        (t for t in UNIVERSITY_TARGETS
         if t.university == item.university and t.college == item.college),
        None,
    )
    if not target:
        return []

    urls = list(dict.fromkeys(
        [u for u in target.news_urls if u]
        + (derive_news_urls(target.base_url) if target.base_url else [])
    ))
    found: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    for page_url in urls[:3]:
        html = await fetch_page(page_url, allow_playwright=False, fast=True)
        if not html or is_blocked_html(html):
            continue
        base = target.base_url or page_url
        for cand in parse_news_list(
            html, base, target.college_type, board=board, phase=phase,
        ):
            if cand.url in seen:
                continue
            seen.add(cand.url)
            cand.university = item.university
            cand.college = item.college
            cand.college_type = item.college_type
            found.append(cand)
    return found


async def resolve_better_url(
    item: ParsedAnnouncement,
    *,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
    client: httpx.AsyncClient | None = None,
    mode: EnrichMode = "normal",
) -> bool:
    """缺截止填报或 URL 可疑时，从学院通知栏 + Bing 重新定位正确详情页。"""
    if mode == "light":
        return False

    original = item.url or ""
    candidates: list[tuple[float, ParsedAnnouncement, str | None]] = []

    for cand in await _crawl_college_news(item, board, phase):
        score = score_notice_candidate(cand, item)
        if score < 0:
            continue
        candidates.append((score, cand, None))

    for cand in await _bing_candidates(item, board, phase, client=client, mode=mode):
        score = score_notice_candidate(cand, item, html=None)
        if score < 0:
            continue
        candidates.append((score + (cand.deadline and 5 or 0), cand, cand.summary))

    if not candidates:
        return False

    candidates.sort(key=lambda x: -x[0])
    tried: set[str] = set()
    max_try = settings.enrich_resolve_max_candidates

    for score, cand, snippet in candidates[:max_try]:
        if cand.url in tried or cand.url == original:
            continue
        tried.add(cand.url)
        if is_listing_url(cand.url) or not is_valid_announcement_url(cand.url):
            continue

        allow_pw = mode == "full"
        fast = settings.crawl_fast_mode and mode != "full"
        html = await fetch_page(
            cand.url, client=client, allow_playwright=allow_pw, fast=fast,
        )
        if html and not is_blocked_html(html):
            probe = ParsedAnnouncement(
                title=cand.title or item.title,
                url=cand.url,
                university=item.university,
                college=item.college,
                college_type=item.college_type,
            )
            enrich_from_html(probe, html)
            html_score = score_notice_candidate(probe, item, html=html)
            if probe.deadline or html_score >= score:
                item.url = cand.url
                item.title = cand.title or item.title
                item.deadline = probe.deadline or item.deadline
                item.publish_date = probe.publish_date or item.publish_date
                item.event_time = probe.event_time or item.event_time
                item.event_format = probe.event_format or item.event_format
                if probe.summary:
                    item.summary = probe.summary
                logger.info("重新定位详情页 (score=%.1f): %s", html_score, cand.url)
                return True

        if snippet and _apply_snippet(item, cand.title, snippet) and item.deadline:
            item.url = cand.url
            item.title = cand.title or item.title
            logger.info("重新定位使用摘要: %s", cand.title[:40])
            return True

    return False


async def enrich_via_bing(
    item: ParsedAnnouncement,
    *,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
    client: httpx.AsyncClient | None = None,
    mode: EnrichMode = "normal",
) -> bool:
    """反爬/列表页无法解析时，用 Bing 找详情页或摘要补全。"""
    candidates = await _bing_candidates(item, board, phase, client=client, mode=mode)
    if not candidates:
        return False

    allow_pw = mode == "full"
    fast = settings.crawl_fast_mode and mode != "full"
    for cand in candidates[:6]:
        if is_listing_url(cand.url):
            continue
        if not is_valid_announcement_url(cand.url):
            continue
        if is_recap_notice(cand.title) or is_stale_url(cand.url):
            continue
        if is_aggregate_notice(cand.title, cand.url):
            continue
        if item.university and item.university not in cand.title:
            uni_hit = item.university[:2] in cand.title
            col_hit = item.college and item.college[:2] in cand.title
            if not uni_hit and not col_hit:
                continue

        if cand.summary and _apply_snippet(item, cand.title, cand.summary) and item.deadline:
            if cand.url and not is_listing_url(cand.url):
                item.url = cand.url
            logger.info("Bing 兜底使用摘要: %s", cand.title[:40])
            return True

        html = await fetch_page(cand.url, client=client, allow_playwright=allow_pw, fast=fast)
        if html and not is_blocked_html(html):
            item.url = cand.url
            item.title = cand.title or item.title
            enrich_from_html(item, html)
            logger.info("Bing 兜底命中详情页: %s", cand.url)
            return True

    return False


async def _enrich_announcement_impl(
    item: ParsedAnnouncement,
    *,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
    client: httpx.AsyncClient | None = None,
    mode: EnrichMode = "normal",
) -> ParsedAnnouncement:
    original_url = item.url or ""
    url = original_url
    fast = settings.crawl_fast_mode and mode != "full"

    if item.summary and _needs_enrich(item):
        _apply_snippet(item, item.title or "", item.summary)

    if mode == "light" and core_times_complete(item) and item.event_format:
        return item

    if not url:
        if item.title and mode not in ("light",):
            await enrich_via_bing(item, board=board, phase=phase, client=client, mode=mode)
        return item

    if is_attachment_url(url):
        att_text = await fetch_attachment_text(url, client=client, title=item.title or "")
        if att_text:
            if settings.llm_enabled and settings.llm_classify_enabled:
                from crawler.llm_classifier import classify_notice_relevance

                clf = classify_notice_relevance(
                    item.title or "", att_text,
                    college_type=item.college_type or "law",
                    url=url,
                )
                if not clf.relevant and clf.failure_type == "success":
                    item.llm_rejected = True
                    return item
            if not item.summary or len(item.summary or "") < 80:
                item.summary = att_text[:500]
            if settings.llm_enabled and settings.llm_extract_first:
                from crawler.llm_extractor import merge_llm_then_regex

                merge_llm_then_regex(item, att_text, url=url)
            else:
                merge_times_into_item(item, att_text)
            if not item.publish_date:
                item.publish_date = extract_date_from_url(url)
            return item

    level, reason = assess_notice_url(item)
    if level == "bad" and mode not in ("light", "crawl"):
        logger.info("URL 不可信(%s)，尝试重新定位: %s", reason, url[:70])
        if await resolve_better_url(item, board=board, phase=phase, client=client, mode=mode):
            _remember_url_change(item, original_url)
            if item.deadline:
                return item
        url = item.url or original_url

    if is_listing_url(url) and mode not in ("light", "crawl"):
        if await enrich_via_bing(item, board=board, phase=phase, client=client, mode=mode):
            _remember_url_change(item, original_url)
            if not _needs_enrich(item):
                return item

    allow_pw = mode == "full"
    html = await fetch_page(
        url, client=client, allow_playwright=allow_pw, fast=fast,
    )

    if html and not is_blocked_html(html):
        if is_pdf_url(url):
            if not _apply_content_enrich(item, html, url):
                return item
        elif is_listing_page(html, url, item.title or ""):
            picked = pick_detail_from_listing(
                html,
                url,
                title=item.title or "",
                university=item.university or "",
                college=item.college or "",
                college_type=item.college_type or "law",
                board=board,
                phase=phase,
            )
            if picked and picked.url and picked.url != url:
                item.url = picked.url
                item.title = picked.title or item.title
                _remember_url_change(item, original_url)
                detail_html = await fetch_page(
                    picked.url, client=client, allow_playwright=allow_pw, fast=fast,
                )
                if detail_html and not is_blocked_html(detail_html):
                    if not _apply_content_enrich(item, detail_html, picked.url):
                        return item
                else:
                    if not _apply_content_enrich(item, html, url):
                        return item
            else:
                if not _apply_content_enrich(item, html, url):
                    return item
        else:
            if not _apply_content_enrich(item, html, url):
                return item
    elif not item.publish_date:
        item.publish_date = extract_date_from_url(url)

    if _needs_enrich(item) and mode in ("normal", "full", "crawl"):
        await enrich_via_bing(item, board=board, phase=phase, client=client, mode=mode)
        _remember_url_change(item, original_url)

    if not item.deadline and should_reresolve_url(item, html=html) and mode == "full":
        await resolve_better_url(item, board=board, phase=phase, client=client, mode=mode)
        _remember_url_change(item, original_url)

    if not item.publish_date:
        item.publish_date = extract_date_from_url(item.url or url)

    return item


async def enrich_announcement(
    item: ParsedAnnouncement,
    *,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
    client: httpx.AsyncClient | None = None,
    mode: EnrichMode = "normal",
) -> ParsedAnnouncement:
    """
    完整补全流程（mode）：
    - light：摘要解析，无详情抓取（仅预览）
    - crawl：详情页抓取 + 限量 Bing（检索入库默认）
    - normal：通知栏 + Bing
    - full：Playwright + 完整重定位（回填脚本）
    """
    timeout = settings.enrich_timeout_sec
    if mode == "light":
        timeout = settings.enrich_timeout_light_sec
    elif mode == "crawl":
        timeout = min(timeout, 25)
    elif mode == "full":
        timeout = settings.enrich_timeout_full_sec
    try:
        return await asyncio.wait_for(
            _enrich_announcement_impl(
                item, board=board, phase=phase, client=client, mode=mode,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "补全超时 (%ds): %s %s",
            timeout,
            item.university,
            (item.title or "")[:40],
        )
        if item.summary and _needs_enrich(item):
            _apply_snippet(item, item.title or "", item.summary)
        return item


def infer_board_from_item(item: ParsedAnnouncement) -> str:
    if item.event_type and "预推免" in item.event_type:
        return PRE_ADMISSION
    if item.title and "预推免" in item.title:
        return PRE_ADMISSION
    return SUMMER_CAMP
