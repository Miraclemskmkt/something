"""HTTP 抓取：浏览器头、会话 Referer、反爬检测与重试。"""
import asyncio
import logging

import httpx

from config import settings
from crawler.anti_crawl import (
    browser_headers,
    create_http_client,
    domain_of,
    is_blocked_html,
    is_retryable_status,
    prefers_mobile_first,
    request_jitter,
    retry_delay,
    site_root,
)

logger = logging.getLogger(__name__)

__all__ = [
    "browser_headers",
    "create_http_client",
    "fetch_page",
    "is_blocked_html",
    "site_root",
]

_warmed_hosts: set[str] = set()


async def _fetch_once(
    client: httpx.AsyncClient,
    url: str,
    *,
    mobile: bool,
    fast: bool,
) -> str | None:
    root = site_root(url)
    host = domain_of(url)
    try:
        if not fast and host not in _warmed_hosts:
            await client.get(root, headers=browser_headers(root, mobile=mobile))
            _warmed_hosts.add(host)
        resp = await client.get(
            url,
            headers=browser_headers(url, referer=root, mobile=mobile),
        )
        if not (200 <= resp.status_code < 400) and not is_retryable_status(resp.status_code):
            return None
        resp.encoding = resp.charset_encoding or "utf-8"
        text = resp.text
        if text and not is_blocked_html(text):
            return text
        if is_retryable_status(resp.status_code):
            logger.debug("Retryable status %s for %s", resp.status_code, url[:80])
    except Exception as e:
        logger.debug("Fetch error %s: %s", url, e)
    return None


async def _fetch_page_impl(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    allow_playwright: bool = True,
    fast: bool | None = None,
) -> str | None:
    if not url or not url.startswith("http"):
        return None

    from crawler.url_quality import is_pdf_url

    if is_pdf_url(url):
        from crawler.pdf_extractor import fetch_pdf_text

        return await fetch_pdf_text(url, client=client)

    if fast is None:
        fast = settings.crawl_fast_mode

    attempts = 1 if fast else max(1, settings.fetch_retry_count + 1)
    if prefers_mobile_first(url):
        order = (True, False) if not fast else (True,)
    else:
        order = (False, True) if not fast else (False,)

    async def _run(c: httpx.AsyncClient) -> str | None:
        for attempt in range(attempts):
            if attempt and not fast:
                await retry_delay(attempt - 1)
            elif not fast:
                await request_jitter()

            for mobile in order:
                text = await _fetch_once(c, url, mobile=mobile, fast=fast)
                if text:
                    return text
                if not fast:
                    await request_jitter()
        return None

    text: str | None = None
    if client is not None:
        text = await _run(client)
    else:
        async with create_http_client() as c:
            text = await _run(c)

    if text:
        return text

    if allow_playwright and settings.playwright_enabled and not fast:
        from crawler.playwright_fetcher import fetch_with_playwright

        return await fetch_with_playwright(url)
    return None


async def fetch_page(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    allow_playwright: bool = True,
    fast: bool | None = None,
) -> str | None:
    """抓取页面：Referer 预热 → 桌面/移动 UA → 重试 → Playwright（可选）。"""
    timeout = settings.fetch_page_timeout
    try:
        return await asyncio.wait_for(
            _fetch_page_impl(
                url, client=client, allow_playwright=allow_playwright, fast=fast,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.debug("页面抓取超时 (%ds): %s", timeout, url[:80])
        return None
