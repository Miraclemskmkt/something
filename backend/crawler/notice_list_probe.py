"""自动探测学院通知列表页（HEAD 优先、并发探测、请求预算）。"""

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config import settings
from crawler.anti_crawl import browser_headers, site_root
from crawler.request_budget import CollegeBudget

logger = logging.getLogger(__name__)

NOTICE_PATH_TEMPLATES = [
    "/xygg/list.htm",
    "/tzgg.htm",
    "/tzgg/list.htm",
    "/xwzx/tzgg/index.htm",
    "/xwzx/xygg.htm",
    "/info/1012/list.htm",
    "/882/list.htm",
    "/Data/List/tzgg",
    "/news/tzgg/index.htm",
    "/glfw/tzgg.htm",
    "/zsxx.htm",
]

LIST_PAGE_HINTS = ("通知", "公告", "夏令营", "招生", "推免", "预推免", "开放日")
ANCHOR_HINTS = ("通知", "公告", "招生", "新闻", "tzgg", "xygg", "list.htm")

_HEAD_TIMEOUT = httpx.Timeout(
    connect=settings.http_connect_timeout,
    read=settings.http_head_timeout,
    write=settings.http_head_timeout,
    pool=settings.http_connect_timeout,
)
_PROBE_READ = min(settings.http_read_timeout, 2.5)
_PROBE_GET_TIMEOUT = httpx.Timeout(
    connect=settings.http_connect_timeout,
    read=_PROBE_READ,
    write=_PROBE_READ,
    pool=settings.http_connect_timeout,
)


async def quick_reachable(
    url: str,
    client: httpx.AsyncClient,
    budget: CollegeBudget | None = None,
) -> bool:
    if not url or not url.startswith("http"):
        return False
    if budget and not budget.can_request():
        return False
    if budget:
        budget.consume()
    try:
        resp = await client.head(
            url,
            headers=browser_headers(url, referer=site_root(url)),
            follow_redirects=True,
            timeout=_HEAD_TIMEOUT,
        )
        return 200 <= resp.status_code < 400
    except Exception:
        return False


def _looks_like_list_page(text: str) -> bool:
    if not text or len(text) < 100:
        return False
    sample = text[:8000]
    hits = sum(1 for h in LIST_PAGE_HINTS if h in sample)
    has_links = bool(re.search(r'href=["\'][^"\']+(?:info|list|page|article|content)', sample, re.I))
    return hits >= 2 or (hits >= 1 and has_links)


async def _head_ok(url: str, client: httpx.AsyncClient) -> bool:
    try:
        resp = await client.head(
            url,
            headers=browser_headers(url, referer=site_root(url)),
            follow_redirects=True,
            timeout=_HEAD_TIMEOUT,
        )
        return 200 <= resp.status_code < 400
    except Exception:
        return False


async def _probe_url_get(
    url: str,
    client: httpx.AsyncClient,
    budget: CollegeBudget | None,
) -> bool:
    if budget and not budget.can_request():
        return False
    if budget:
        budget.consume()
    try:
        resp = await client.get(
            url,
            headers=browser_headers(url, referer=site_root(url)),
            follow_redirects=True,
            timeout=_PROBE_GET_TIMEOUT,
        )
        if resp.status_code != 200:
            return False
        return _looks_like_list_page(resp.text or "")
    except Exception:
        return False


async def discover_notice_list_urls(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    existing: list[str] | None = None,
    budget: CollegeBudget | None = None,
) -> list[str]:
    """并发 HEAD 探测常见路径，命中后再 GET 校验内容。"""
    found: list[str] = list(dict.fromkeys(existing or []))
    seen = set(found)
    if not base_url:
        return found

    root = base_url.rstrip("/")
    paths = NOTICE_PATH_TEMPLATES[: settings.notice_probe_max_paths]
    parallel = settings.notice_probe_parallel_paths

    for i in range(0, len(paths), parallel):
        if budget and budget.expired():
            break
        batch_paths = paths[i: i + parallel]
        urls = [
            root + p if p.startswith("/") else f"{root}/{p}"
            for p in batch_paths
            if (root + p if p.startswith("/") else f"{root}/{p}") not in seen
        ]
        if not urls:
            continue

        head_tasks = [_head_ok(u, client) for u in urls]
        head_ok = await asyncio.gather(*head_tasks)
        head_pass = [u for u, ok in zip(urls, head_ok) if ok][:2]
        if head_pass:
            get_tasks = [_probe_url_get(u, client, budget) for u in head_pass]
            get_ok = await asyncio.gather(*get_tasks)
            for url, ok in zip(head_pass, get_ok):
                if ok:
                    found.append(url)
                    seen.add(url)
                    logger.info("探测到通知列表: %s", url[:80])
                    return found[:1]

    if found:
        return found

    if budget and (budget.expired() or not budget.can_request()):
        return found

    try:
        if budget:
            budget.consume()
        resp = await client.get(
            base_url,
            headers=browser_headers(base_url, referer=site_root(base_url)),
            follow_redirects=True,
            timeout=_PROBE_GET_TIMEOUT,
        )
        if resp.status_code == 200 and resp.text:
            probe_links: list[str] = []
            for link in _extract_notice_links_from_home(resp.text, base_url)[:2]:
                if link in seen:
                    continue
                seen.add(link)
                probe_links.append(link)
            if probe_links:
                get_ok = await asyncio.gather(
                    *[_probe_url_get(link, client, budget) for link in probe_links],
                )
                for link, ok in zip(probe_links, get_ok):
                    if ok:
                        found.append(link)
                        return found[:1]
    except Exception as e:
        logger.debug("首页链接探测失败 %s: %s", base_url, e)

    return found


def _extract_notice_links_from_home(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        text = (a.get_text(strip=True) or "") + " " + a["href"]
        score = sum(1 for h in ANCHOR_HINTS if h in text)
        if score <= 0:
            continue
        url = urljoin(base_url, a["href"])
        if "edu.cn" not in urlparse(url).netloc:
            continue
        scored[url] = scored.get(url, 0) + score
    return [u for u, _ in sorted(scored.items(), key=lambda x: -x[1])[:5]]
