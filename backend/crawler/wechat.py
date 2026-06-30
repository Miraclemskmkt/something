"""微信公众号发现：搜狗微信搜索 + 正文补全。"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx

from config import settings
from crawler.boards import wechat_keywords
from crawler.parser import ParsedAnnouncement, parse_wechat_search_results
from crawler.wechat_state import (
    college_searched_today,
    record_sogou_failure,
    record_sogou_search,
    should_use_sogou,
)

logger = logging.getLogger(__name__)

SOGOU_BASE = "https://weixin.sogou.com"


def _sogou_headers(referer: str = SOGOU_BASE) -> dict[str, str]:
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    }
    if settings.sogou_cookie.strip():
        headers["Cookie"] = settings.sogou_cookie.strip()
    return headers


def _sogou_search_url(query: str) -> str:
    return (
        f"{SOGOU_BASE}/weixin?ie=utf8&s_from=input&type=2&query={quote(query)}"
    )


async def _warmup_sogou(client: httpx.AsyncClient) -> None:
    """模拟浏览器先打开首页再搜索，降低 antispider 概率。"""
    try:
        await client.get(SOGOU_BASE + "/", headers=_sogou_headers())
        await asyncio.sleep(0.8)
    except Exception:
        pass


def _is_antispider(html: str, url: str) -> bool:
    text = html or ""
    final_url = url or ""
    return (
        "验证码" in text
        or "antispider" in text
        or "antispider" in final_url
        or "请输入验证码" in text
    )


def build_sogou_queries(target, board: str, phase: str) -> list[str]:
    """构造搜狗微信搜索词：学校+学院+夏令营+年份（主力）+ 公众号名（备选）。"""
    from crawler.university_config import get_wechat_account

    year = settings.min_notice_year
    queries: list[str] = []

    # 主力：学院全称搜索
    queries.append(f"{target.university} {target.college} 夏令营 {year}")

    if board != "summer_camp" or phase != "notice":
        queries.append(f"{target.university} {target.college} 预推免 {year}")

    # 备选：公众号名 + 关键词
    account = get_wechat_account(target)
    for kw in wechat_keywords(board, phase):
        queries.append(f"{account} {kw}")

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    limit = 1 if settings.wechat_compact_keywords else settings.wechat_sogou_queries_per_college
    return out[:limit]


async def search_wechat_for_target(
    target, board: str, phase: str, *, force: bool = False,
) -> list[ParsedAnnouncement]:
    if not should_use_sogou(force=force):
        return []

    if not force and college_searched_today(target.university, target.college):
        logger.debug("搜狗微信：今日已搜 %s %s，跳过", target.university, target.college)
        return []

    results: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    captcha = False

    async with httpx.AsyncClient(
        timeout=settings.request_timeout,
        follow_redirects=True,
        headers=_sogou_headers(),
    ) as client:
        await _warmup_sogou(client)
        for query in build_sogou_queries(target, board, phase):
            if not should_use_sogou(force=force):
                break

            url = _sogou_search_url(query)
            try:
                await asyncio.sleep(settings.wechat_request_delay)
                resp = None
                html = ""
                final_url = url

                if settings.sogou_cookie.strip() or settings.sogou_cdp_url.strip():
                    try:
                        from crawler.wechat_sogou_pw import fetch_sogou_weixin_html
                        html, final_url = await fetch_sogou_weixin_html(query)
                    except Exception as e:
                        logger.debug("Playwright 搜狗失败，回退 httpx: %s", e)

                if not html:
                    resp = await client.get(url, headers=_sogou_headers(referer=SOGOU_BASE + "/"))
                    html = resp.text
                    final_url = str(resp.url)
                else:
                    class _Resp:
                        text = html
                        url = final_url
                        status_code = 200
                    resp = _Resp()

                record_sogou_search(target.university, target.college)

                if getattr(resp, "status_code", 200) != 200:
                    record_sogou_failure(target.university, target.college, f"HTTP {resp.status_code}")
                    continue

                if _is_antispider(html, final_url):
                    captcha = True
                    record_sogou_search(target.university, target.college, captcha=True)
                    record_sogou_failure(target.university, target.college, "验证码")
                    logger.warning("搜狗微信验证码，放弃本次: %s", query)
                    break

                items = parse_wechat_search_results(
                    html, target, SOGOU_BASE, board=board, phase=phase,
                )
                for item in items:
                    resolved = await resolve_weixin_url(client, item.url)
                    if resolved:
                        item.url = resolved
                    fp = resolved or item.url
                    if fp not in seen:
                        seen.add(fp)
                        results.append(item)

                if results:
                    break
            except Exception as e:
                record_sogou_failure(target.university, target.college, str(e))
                logger.warning("WeChat search error '%s': %s", query, e)

    if captcha and not settings.sogou_cookie.strip():
        logger.info(
            "提示：可在 backend/.env 设置 CAMP_SOGOU_COOKIE 以降低验证码（浏览器登录搜狗微信后复制 Cookie）"
        )

    return results


async def resolve_weixin_url(client: httpx.AsyncClient, url: str) -> str | None:
    if "mp.weixin.qq.com" in url:
        return url.split("#")[0].split("&chksm")[0] if "?" in url else url.split("#")[0]
    try:
        resp = await client.get(url, follow_redirects=True)
        final = str(resp.url).split("#")[0]
        if "mp.weixin.qq.com" in final:
            return final
    except Exception:
        pass
    return None


async def enrich_wechat_items(
    items: list[ParsedAnnouncement],
    *,
    client: httpx.AsyncClient | None = None,
    board: str = "summer_camp",
    phase: str = "notice",
) -> list[ParsedAnnouncement]:
    """抓取微信正文、走 LLM 管线补全四字段。"""
    from crawler.field_enricher import enrich_parsed_item
    from crawler.wechat_article import fetch_weixin_article, is_poster_only

    sem = asyncio.Semaphore(2)

    async def one(item: ParsedAnnouncement) -> ParsedAnnouncement | None:
        async with sem:
            c = client
            if c:
                final, title, text, _ = await fetch_weixin_article(item.url, client=c)
            else:
                async with httpx.AsyncClient(timeout=settings.request_timeout) as tmp:
                    final, title, text, _ = await fetch_weixin_article(item.url, client=tmp)
            if final:
                item.url = final
            if title and (not item.title or len(title) > len(item.title)):
                item.title = title
            if text:
                item.summary = text[:500]
            if is_poster_only(text):
                item.summary = (item.summary or "") + " [图片海报，需人工查看]"
            await enrich_parsed_item(item, client=client, force_llm=bool(text))
            if item.llm_rejected:
                return None
            return item

    if client:
        results = await asyncio.gather(*[one(it) for it in items])
    else:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as c:
            results = await asyncio.gather(*[one(it) for it in items])

    return [r for r in results if r is not None]


async def crawl_wechat_for_targets(
    targets: list,
    board: str,
    phase: str,
    *,
    force: bool = False,
) -> list[ParsedAnnouncement]:
    if not settings.wechat_enabled:
        logger.info("微信检索已关闭（CAMP_WECHAT_ENABLED=false）")
        return []
    if not targets:
        return []

    cookie_hint = "已配置 Cookie" if settings.sogou_cookie.strip() else "未配置 Cookie"
    logger.info(
        "搜狗微信：%d 个学院，间隔 %.1fs，并发 %d，日限 %d，%s",
        len(targets),
        settings.wechat_request_delay,
        settings.wechat_max_concurrent,
        settings.wechat_sogou_daily_limit,
        cookie_hint,
    )

    sem = asyncio.Semaphore(max(1, settings.wechat_max_concurrent))
    all_results: list[ParsedAnnouncement] = []
    seen: set[str] = set()

    async def crawl_one(target):
        async with sem:
            return await search_wechat_for_target(target, board, phase, force=force)

    raw = await asyncio.gather(*[crawl_one(t) for t in targets], return_exceptions=True)
    for result in raw:
        if isinstance(result, Exception):
            continue
        for item in result:
            from crawler.wechat_article import weixin_fingerprint
            fp = weixin_fingerprint(item.url)
            if fp not in seen:
                seen.add(fp)
                all_results.append(item)

    logger.info("搜狗微信发现 %d 条，开始抓取正文…", len(all_results))

    if all_results:
        from crawler.fetcher import create_http_client
        async with create_http_client() as client:
            all_results = await enrich_wechat_items(
                all_results, client=client, board=board, phase=phase,
            )

    logger.info("微信检索完成：有效 %d 条", len(all_results))
    return all_results
