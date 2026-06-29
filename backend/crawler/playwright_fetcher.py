"""Optional Playwright fallback for JS/WAF-protected pages (lazy import)."""
import asyncio
import logging

from config import settings
from crawler.fetcher import is_blocked_html, site_root

logger = logging.getLogger(__name__)

_browser = None
_playwright = None
_lock = asyncio.Lock()


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
            _browser = await _playwright.chromium.launch(headless=True)
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


async def fetch_with_playwright(url: str) -> str | None:
    """用无头 Chromium 渲染页面，绕过部分 JS 反爬。"""
    if not settings.playwright_enabled or not url:
        return None

    browser = await _ensure_browser()
    if not browser:
        return None

    timeout_ms = settings.playwright_timeout * 1000
    wait_ms = settings.playwright_wait_ms

    try:
        context = await browser.new_context(
            user_agent=settings.user_agent,
            locale="zh-CN",
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        page = await context.new_page()
        root = site_root(url)
        try:
            await page.goto(
                root,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
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
        # WAF 挑战页（如 HTTP 202）需等待 JS 跳转后再取正文
        for _ in range(4):
            html = await page.content()
            if html and not is_blocked_html(html):
                break
            await asyncio.sleep(2)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        html = await page.content()
        await context.close()
        if html and not is_blocked_html(html):
            logger.info("Playwright 抓取成功: %s", url[:80])
            return html
    except Exception as e:
        logger.debug("Playwright fetch failed %s: %s", url, e)
    return None
