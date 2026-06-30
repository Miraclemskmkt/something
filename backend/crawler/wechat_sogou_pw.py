"""Playwright 抓取搜狗微信搜索页（Cookie 在浏览器有效但 httpx 被 antispider 时使用）。"""
from __future__ import annotations

import logging
from urllib.parse import quote

from config import settings

logger = logging.getLogger(__name__)

SOGOU_BASE = "https://weixin.sogou.com"

_STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""

_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-infobars",
)

# name -> domain
_COOKIE_DOMAINS: dict[str, str] = {
    "PHPSESSID": "weixin.sogou.com",
    "ANTIST": ".www.sogou.com",
}


def _cookie_domain(name: str) -> str:
    if name in _COOKIE_DOMAINS:
        return _COOKIE_DOMAINS[name]
    if name in ("SUV", "IPLOC", "cuid"):
        return ".sogou.com"
    return ".weixin.sogou.com"


def parse_sogou_cookie_string(cookie_str: str) -> list[dict]:
    out: list[dict] = []
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        out.append({
            "name": name,
            "value": value,
            "domain": _cookie_domain(name),
            "path": "/",
        })
    return out


async def _launch_browser(playwright):
    for channel in ("msedge", "chrome", None):
        try:
            kwargs = {"headless": True, "args": list(_LAUNCH_ARGS)}
            if channel:
                kwargs["channel"] = channel
            return await playwright.chromium.launch(**kwargs)
        except Exception as e:
            logger.debug("Playwright launch channel=%s failed: %s", channel, e)
    return None


async def _fetch_via_cdp(query: str) -> tuple[str, str]:
    from playwright.async_api import async_playwright

    url = f"{SOGOU_BASE}/weixin?ie=utf8&s_from=input&type=2&query={quote(query)}"
    cdp = (settings.sogou_cdp_url or "").strip()
    if not cdp:
        return "", url

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp)
        try:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(SOGOU_BASE + "/", wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(800)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
            return await page.content(), page.url
        finally:
            await browser.close()


async def fetch_sogou_weixin_html(query: str) -> tuple[str, str]:
    """
    用 Playwright + 用户 Cookie 或 CDP 已登录浏览器拉取搜狗微信搜索结果 HTML。
    返回 (html, final_url)。
    """
    html, final = await _fetch_via_cdp(query)
    if html and "antispider" not in final and "antispider" not in html:
        return html, final

    from playwright.async_api import async_playwright

    url = f"{SOGOU_BASE}/weixin?ie=utf8&s_from=input&type=2&query={quote(query)}"
    cookies = parse_sogou_cookie_string(settings.sogou_cookie)
    if not cookies:
        return html, final or url

    async with async_playwright() as p:
        browser = await _launch_browser(p)
        if not browser:
            return html, final or url
        try:
            context = await browser.new_context(
                user_agent=settings.user_agent,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
            )
            await context.add_init_script(_STEALTH_INIT)
            await context.add_cookies(cookies)
            page = await context.new_page()
            await page.goto(SOGOU_BASE + "/", wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(1500)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000, referer=SOGOU_BASE + "/")
            await page.wait_for_timeout(2000)
            html = await page.content()
            final = page.url
            await context.close()
            return html, final
        finally:
            await browser.close()
