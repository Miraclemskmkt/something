"""Optional Playwright fallback for JS/WAF-protected pages (lazy import)."""
import asyncio
import logging

from config import settings
from crawler.anti_crawl import (
    MOBILE_UA,
    browser_headers,
    is_blocked_html,
    site_root,
)

logger = logging.getLogger(__name__)

_browser = None
_playwright = None
_lock = asyncio.Lock()

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


async def _ensure_browser():
    global _browser, _playwright
    async with _lock:
        if _browser is not None:
            return _browser
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning(
                "Playwright 未安装：pip install playwright && playwright install chromium"
            )
            return None
        try:
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=list(_LAUNCH_ARGS),
            )
            return _browser
        except Exception as e:
            logger.warning("Playwright 启动失败: %s", e)
            return None


async def close_playwright() -> None:
    global _browser, _playwright
    async with _lock:
        if _browser:
            try:
                await _browser.close()
            except Exception:
                pass
            _browser = None
        if _playwright:
            try:
                await _playwright.stop()
            except Exception:
                pass
            _playwright = None


async def _fetch_in_context(
    browser,
    url: str,
    *,
    mobile: bool,
) -> str | None:
    timeout_ms = settings.playwright_timeout * 1000
    wait_ms = settings.playwright_wait_ms
    ua = MOBILE_UA if mobile else settings.user_agent
    root = site_root(url)

    context = await browser.new_context(
        user_agent=ua,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport={"width": 390, "height": 844} if mobile else {"width": 1920, "height": 1080},
        is_mobile=mobile,
        has_touch=mobile,
        ignore_https_errors=True,
        extra_http_headers={
            k: v
            for k, v in browser_headers(url, referer=root, mobile=mobile).items()
            if k.lower() not in ("host", "content-length")
        },
    )
    await context.add_init_script(_STEALTH_INIT)
    page = await context.new_page()

    try:
        try:
            await page.goto(root, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
            referer=root,
        )
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000)

        html = ""
        for _ in range(5):
            html = await page.content()
            if html and not is_blocked_html(html):
                break
            await asyncio.sleep(2)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

        if html and not is_blocked_html(html):
            logger.info("Playwright 抓取成功 (%s): %s", "mobile" if mobile else "desktop", url[:80])
            return html
    except Exception as e:
        logger.debug("Playwright fetch failed %s: %s", url, e)
    finally:
        await context.close()
    return None


async def fetch_with_playwright(url: str) -> str | None:
    """用无头 Chromium 渲染页面，绕过部分 JS 反爬。"""
    if not settings.playwright_enabled or not url:
        return None

    browser = await _ensure_browser()
    if not browser:
        return None

    from crawler.anti_crawl import prefers_mobile_first

    order = (True, False) if prefers_mobile_first(url) else (False, True)
    for mobile in order:
        html = await _fetch_in_context(browser, url, mobile=mobile)
        if html:
            return html
    return None
