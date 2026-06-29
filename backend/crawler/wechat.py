import asyncio
import logging
from urllib.parse import quote

import httpx

from config import settings
from crawler.boards import wechat_keywords
from crawler.parser import ParsedAnnouncement, parse_wechat_search_results

logger = logging.getLogger(__name__)

SOGOU_BASE = "https://weixin.sogou.com"


def _sogou_headers() -> dict[str, str]:
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": SOGOU_BASE,
    }
    if settings.sogou_cookie.strip():
        headers["Cookie"] = settings.sogou_cookie.strip()
    return headers


def _is_antispider(html: str, url: str) -> bool:
    text = html or ""
    final_url = url or ""
    return "验证码" in text or "antispider" in text or "antispider" in final_url


async def search_wechat_for_target(target, board: str, phase: str) -> list[ParsedAnnouncement]:
    from crawler.university_config import get_wechat_account

    account = get_wechat_account(target)
    results: list[ParsedAnnouncement] = []
    seen: set[str] = set()
    keywords = wechat_keywords(board, phase)
    captcha_hits = 0

    async with httpx.AsyncClient(
        timeout=settings.request_timeout,
        follow_redirects=True,
        headers=_sogou_headers(),
    ) as client:
        for keyword in keywords:
            query = f"{account} {keyword}"
            url = f"{SOGOU_BASE}/weixin?type=2&query={quote(query)}"
            try:
                await asyncio.sleep(settings.wechat_request_delay)
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                if _is_antispider(resp.text, str(resp.url)):
                    captcha_hits += 1
                    logger.warning("搜狗微信触发验证码，跳过: %s", query)
                    continue

                items = parse_wechat_search_results(
                    resp.text, target, SOGOU_BASE, board=board, phase=phase,
                )
                for item in items:
                    resolved = await resolve_weixin_url(client, item.url)
                    if resolved:
                        item.url = resolved
                    if item.url not in seen:
                        seen.add(item.url)
                        results.append(item)
            except Exception as e:
                logger.warning("WeChat search error '%s': %s", query, e)

    if captcha_hits and not settings.sogou_cookie.strip():
        logger.info(
            "提示：可在 backend/.env 设置 CAMP_SOGOU_COOKIE 以降低验证码频率（浏览器登录搜狗微信后复制 Cookie）"
        )

    return results


async def resolve_weixin_url(client: httpx.AsyncClient, url: str) -> str | None:
    if "mp.weixin.qq.com" in url:
        return url.split("&")[0] if "?" in url else url
    try:
        resp = await client.get(url, follow_redirects=True)
        final = str(resp.url)
        if "mp.weixin.qq.com" in final:
            return final.split("#")[0]
    except Exception:
        pass
    return None


async def crawl_wechat_for_targets(
    targets: list,
    board: str,
    phase: str,
) -> list[ParsedAnnouncement]:
    if not settings.wechat_enabled:
        logger.info("微信检索已关闭（CAMP_WECHAT_ENABLED=false）")
        return []
    if not targets:
        return []

    cookie_hint = "已配置 Cookie" if settings.sogou_cookie.strip() else "未配置 Cookie"
    logger.info(
        "微信检索：%d 个学院，间隔 %.1fs，并发 %d，%s",
        len(targets),
        settings.wechat_request_delay,
        settings.wechat_max_concurrent,
        cookie_hint,
    )

    sem = asyncio.Semaphore(max(1, settings.wechat_max_concurrent))
    all_results: list[ParsedAnnouncement] = []
    seen: set[str] = set()

    async def crawl_one(target):
        async with sem:
            return await search_wechat_for_target(target, board, phase)

    results = await asyncio.gather(*[crawl_one(t) for t in targets], return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            continue
        for item in result:
            if item.url not in seen:
                seen.add(item.url)
                all_results.append(item)

    logger.info("微信检索完成：共 %d 条", len(all_results))
    return all_results
