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
    is_recap_notice,
    is_stale_url,
    score_notice_candidate,
    should_reresolve_url,
)
from crawler.parser import (
    ParsedAnnouncement,
    enrich_from_html,
    extract_date_from_url,
    is_valid_announcement_url,
)

logger = logging.getLogger(__name__)


def _needs_enrich(item: ParsedAnnouncement) -> bool:
    return not (item.deadline and item.event_time and item.event_format and item.publish_date)


def _apply_snippet(item: ParsedAnnouncement, title: str, snippet: str) -> bool:
    from crawler.parser import compact_spaced_text, extract_all_times, extract_event_format

    focused = compact_spaced_text(f"{title} {snippet}")
    times = extract_all_times(focused)
    fmt = extract_event_format(focused)
    changed = False
    for field in ("publish_date", "deadline", "event_time"):
        val = times.get(field)
        if val and not getattr(item, field):
            setattr(item, field, val)
            changed = True
    if fmt and not item.event_format:
        item.event_format = fmt
        changed = True
    if snippet and (not item.summary or len(item.summary or "") < 40):
        item.summary = snippet[:500]
        changed = True
    return changed


def _remember_url_change(item: ParsedAnnouncement, original: str) -> None:
    if item.url and original and item.url != original:
        item.original_url = original


async def _bing_candidates(item: ParsedAnnouncement, board: str, phase: str) -> list[ParsedAnnouncement]:
    from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS
    from crawler.searcher import search_bing

    college_type = item.college_type or "law"
    short_title = (item.title or "")[:36].strip()
    uni = item.university or ""
    col = item.college or ""
    root = UNIVERSITY_ROOT_DOMAINS.get(uni, "")

    queries: list[str] = []
    if board == PRE_ADMISSION:
        queries.extend(bing_site_queries(uni, col, "预推免 2026", root))
    queries.extend(bing_site_queries(uni, col, "夏令营 招生通知 2026", root))
    queries.extend(bing_site_queries(uni, col, "夏令营 报名截止 2026", root))
    queries.extend(bing_site_queries(uni, col, "优秀大学生 暑期夏令营", root))
    if short_title:
        queries.append(f"{short_title} {uni} site:edu.cn")
    if root:
        queries.append(f"site:{root} {uni} {col} 夏令营 2026")

    seen_q: set[str] = set()
    seen_url: set[str] = set()
    merged: list[ParsedAnnouncement] = []
    delay = settings.search_request_delay
    for i, q in enumerate(queries):
        if not q or q in seen_q:
            continue
        seen_q.add(q)
        if i > 0 and delay > 0:
            await asyncio.sleep(delay)
        try:
            batch = await search_bing(q, college_type, board, phase)
        except Exception as e:
            logger.debug("Bing fallback query failed: %s", e)
            continue
        for r in batch:
            if r.url in seen_url:
                continue
            seen_url.add(r.url)
            merged.append(r)
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
    for page_url in urls[:4]:
        html = await fetch_page(page_url)
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
) -> bool:
    """
    缺截止填报或 URL 可疑时，从学院通知栏 + Bing 重新定位正确详情页。
    """
    original = item.url or ""
    candidates: list[tuple[float, ParsedAnnouncement, str | None]] = []

    for cand in await _crawl_college_news(item, board, phase):
        score = score_notice_candidate(cand, item)
        if score < 0:
            continue
        candidates.append((score, cand, None))

    for cand in await _bing_candidates(item, board, phase):
        score = score_notice_candidate(cand, item, html=None)
        if score < 0:
            continue
        candidates.append((score + (cand.deadline and 5 or 0), cand, cand.summary))

    if not candidates:
        return False

    candidates.sort(key=lambda x: -x[0])
    tried: set[str] = set()

    for score, cand, snippet in candidates[:12]:
        if cand.url in tried or cand.url == original:
            continue
        tried.add(cand.url)
        if is_listing_url(cand.url) or not is_valid_announcement_url(cand.url):
            continue

        html = await fetch_page(cand.url, client=client)
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
                logger.info(
                    "重新定位详情页 (score=%.1f): %s",
                    html_score,
                    cand.url,
                )
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
) -> bool:
    """反爬/列表页无法解析时，用 Bing 找详情页或摘要补全。"""
    candidates = await _bing_candidates(item, board, phase)
    if not candidates:
        return False

    for cand in candidates:
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

        html = await fetch_page(cand.url, client=client)
        if html and not is_blocked_html(html):
            item.url = cand.url
            item.title = cand.title or item.title
            enrich_from_html(item, html)
            logger.info("Bing 兜底命中详情页: %s", cand.url)
            return True

        if _apply_snippet(item, cand.title, cand.summary or ""):
            if cand.url and not is_listing_url(cand.url):
                item.url = cand.url
            logger.info("Bing 兜底使用摘要: %s", cand.title[:40])
            return True

    return False


async def enrich_announcement(
    item: ParsedAnnouncement,
    *,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
    client: httpx.AsyncClient | None = None,
) -> ParsedAnnouncement:
    """
    完整补全流程：
    1. 汇总列表 URL → 优先 Bing 下钻
    2. 抓取当前 URL（httpx → Playwright）
    3. 若为汇总列表 → 从 HTML 下钻详情
    4. 反爬空页 / 缺字段 → Bing 兜底
    """
    original_url = item.url or ""
    url = original_url

    if not url:
        if item.title:
            await enrich_via_bing(item, board=board, phase=phase, client=client)
        return item

    level, reason = assess_notice_url(item)
    if level == "bad":
        logger.info("URL 不可信(%s)，尝试重新定位: %s", reason, url[:70])
        if await resolve_better_url(item, board=board, phase=phase, client=client):
            _remember_url_change(item, original_url)
            if item.deadline:
                return item
        url = item.url or original_url

    # 已知汇总入口：HTML 常为空或 JS 渲染，先 Bing 下钻
    if is_listing_url(url):
        if await enrich_via_bing(item, board=board, phase=phase, client=client):
            _remember_url_change(item, original_url)
            if not _needs_enrich(item):
                return item

    html = await fetch_page(url, client=client)

    if html and not is_blocked_html(html):
        if is_listing_page(html, url, item.title or ""):
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
                detail_html = await fetch_page(picked.url, client=client)
                if detail_html and not is_blocked_html(detail_html):
                    enrich_from_html(item, detail_html)
                else:
                    enrich_from_html(item, html)
            else:
                enrich_from_html(item, html)
        else:
            enrich_from_html(item, html)
    elif not item.publish_date:
        item.publish_date = extract_date_from_url(url)

    if _needs_enrich(item):
        await enrich_via_bing(item, board=board, phase=phase, client=client)
        _remember_url_change(item, original_url)

    if not item.deadline and should_reresolve_url(item, html=html):
        await resolve_better_url(item, board=board, phase=phase, client=client)
        _remember_url_change(item, original_url)

    if not item.publish_date:
        item.publish_date = extract_date_from_url(item.url or url)

    return item


def infer_board_from_item(item: ParsedAnnouncement) -> str:
    if item.event_type and "预推免" in item.event_type:
        return PRE_ADMISSION
    if item.title and "预推免" in item.title:
        return PRE_ADMISSION
    return SUMMER_CAMP
