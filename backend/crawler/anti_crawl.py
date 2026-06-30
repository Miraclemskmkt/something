"""反爬策略：浏览器指纹、WAF 识别、请求间隔与重试。"""
from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from config import settings

logger = logging.getLogger(__name__)

# 常见 WAF / JS 挑战页特征（瑞数、阿里云盾、Cloudflare 等）
BLOCKED_MARKERS = (
    "$_ts",
    "$_ss",
    "$_config",
    "vwoNTa",
    "antispider",
    "验证码",
    "challenge-platform",
    "请开启JavaScript",
    "enable JavaScript",
    "Access Verification",
    "cf-browser-verification",
    "Ray ID",
    "安全验证",
    "人机验证",
    "访问验证",
    "系统检测到",
    "您的访问行为异常",
    "acw_sc__v2",
    "aliyun_waf",
    "jsl_clearance",
    "guardret",
    "TS014",
    "waf_captcha",
    "DDoS-GUARD",
    "location.href=location.href",
    "document.cookie=",
    "请完成验证",
    "滑动验证",
    "行为验证",
)

BLOCKED_TITLE_KEYWORDS = (
    "403",
    "404",
    "访问受限",
    "访问拒绝",
    "禁止访问",
    "安全拦截",
    "验证中心",
    "Attention Required",
    "Just a moment",
)

# 部分高校站点对移动 UA 更友好，优先尝试
MOBILE_FIRST_DOMAINS = (
    "scu.edu.cn",
    "zju.edu.cn",
    "nju.edu.cn",
    "whu.edu.cn",
    "sysu.edu.cn",
)

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def site_root(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}/"


def domain_of(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def prefers_mobile_first(url: str) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in MOBILE_FIRST_DOMAINS)


def browser_headers(url: str, *, referer: str | None = None, mobile: bool = False) -> dict[str, str]:
    ua = MOBILE_UA if mobile else (settings.user_agent or DESKTOP_UA)
    root = site_root(url)
    ref = referer or root
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Referer": ref,
    }
    if not mobile:
        headers.update(
            {
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin" if domain_of(ref) == domain_of(url) else "cross-site",
                "Sec-Fetch-User": "?1",
                "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
        )
    return headers


def create_http_client(**overrides) -> httpx.AsyncClient:
    """创建带浏览器头的共享 httpx 客户端（连接池复用 + 分阶段超时）。"""
    pool_size = settings.crawl_college_concurrency + settings.search_max_concurrent
    limits = httpx.Limits(
        max_connections=pool_size,
        max_keepalive_connections=min(32, pool_size),
    )
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=settings.http_read_timeout,
        write=settings.http_read_timeout,
        pool=settings.http_connect_timeout,
    )
    kwargs = {
        "timeout": timeout,
        "follow_redirects": True,
        "verify": False,
        "headers": browser_headers("https://www.edu.cn/"),
        "limits": limits,
    }
    kwargs.update(overrides)
    return httpx.AsyncClient(**kwargs)


async def request_jitter() -> None:
    """请求前随机抖动，降低并发指纹。"""
    jitter = settings.fetch_jitter_max
    if jitter <= 0:
        return
    await asyncio.sleep(random.uniform(0, jitter))


async def retry_delay(attempt: int) -> None:
    base = settings.fetch_retry_delay * (attempt + 1)
    await asyncio.sleep(base + random.uniform(0, settings.fetch_jitter_max))


def is_blocked_html(html: str | None) -> bool:
    """页面是否被 WAF/JS 挑战拦截或几乎无正文。"""
    if not html:
        return True
    from crawler.pdf_extractor import looks_like_html

    if not looks_like_html(html):
        return len(html.strip()) < 80

    if len(html) < 200:
        return True

    head = html[:16000]
    lower = head.lower()
    if any(marker.lower() in lower for marker in BLOCKED_MARKERS):
        return True

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return True

    title = (soup.title.string or "").strip() if soup.title else ""
    if title and any(kw in title for kw in BLOCKED_TITLE_KEYWORDS):
        return True

    text = soup.get_text(strip=True)
    if len(text) < 80:
        return True

    # 瑞数等：正文极短但 script 很多
    scripts = len(soup.find_all("script"))
    if scripts >= 8 and len(text) < 400:
        return True

    return False


def is_retryable_status(status: int) -> bool:
    return status in (202, 403, 429, 500, 502, 503, 504)
