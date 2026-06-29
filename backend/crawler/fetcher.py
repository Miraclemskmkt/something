"""HTTP 抓取：浏览器头、会话 Referer、反爬检测。"""
import logging
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from config import settings

logger = logging.getLogger(__name__)

BLOCKED_MARKERS = (
    "$_ts",
    "vwoNTa",
    "antispider",
    "验证码",
    "challenge-platform",
    "请开启JavaScript",
    "enable JavaScript",
)

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)


def site_root(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}/"


def browser_headers(url: str, *, referer: str | None = None, mobile: bool = False) -> dict[str, str]:
    return {
        "User-Agent": MOBILE_UA if mobile else settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer or site_root(url),
    }


def is_blocked_html(html: str | None) -> bool:
    """页面是否被 WAF/JS 挑战拦截或几乎无正文。"""
    if not html or len(html) < 200:
        return True
    head = html[:12000]
    if any(marker in head for marker in BLOCKED_MARKERS):
        return True
    try:
        text = BeautifulSoup(html, "lxml").get_text(strip=True)
    except Exception:
        return True
    return len(text) < 80


async def fetch_page(url: str, *, client: httpx.AsyncClient | None = None) -> str | None:
    """抓取页面；先带 Referer 会话访问，失败再试移动 UA。"""
    if not url or not url.startswith("http"):
        return None

    async def _get(c: httpx.AsyncClient, mobile: bool = False) -> str | None:
        try:
            root = site_root(url)
            await c.get(root, headers=browser_headers(root))
            resp = await c.get(url, headers=browser_headers(url, referer=root, mobile=mobile))
            if 200 <= resp.status_code < 400:
                resp.encoding = resp.charset_encoding or "utf-8"
                text = resp.text
                if not is_blocked_html(text):
                    return text
        except Exception as e:
            logger.debug("Fetch error %s: %s", url, e)
        return None

    if client is not None:
        text = await _get(client, mobile=False)
        if text:
            return text
        text = await _get(client, mobile=True)
        if text:
            return text
    else:
        timeout = settings.request_timeout
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=False) as c:
            text = await _get(c, mobile=False)
            if text:
                return text
            text = await _get(c, mobile=True)
            if text:
                return text

    if settings.playwright_enabled:
        from crawler.playwright_fetcher import fetch_with_playwright

        return await fetch_with_playwright(url)
    return None
