"""研究生院 / 招生网通知列表探测（按学院名过滤）。"""
from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import httpx

from config import settings
from crawler.anti_crawl import browser_headers, site_root
from crawler.boards import is_relevant_for_crawl
from crawler.domain_fixer import head_reachable
from crawler.grad_school_domains import grad_domain_candidates
from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS
from crawler.parser import ParsedAnnouncement, parse_news_list
from crawler.source_labels import OFFICIAL_LABEL

logger = logging.getLogger(__name__)

LIST_PATHS = (
    "/tzgg/list.htm",
    "/xwzx/tzgg/index.htm",
    "/882/list.htm",
    "/zsxx.htm",
    "/info/1012/list.htm",
    "/news/tzgg/index.htm",
)

_GET_TIMEOUT = httpx.Timeout(
    connect=min(settings.http_connect_timeout, 3.0),
    read=min(settings.http_read_timeout, 3.0),
    write=3.0,
    pool=2.0,
)


def _college_in_title(title: str, college: str, university: str) -> bool:
    t = title or ""
    if college and len(college) >= 3 and college in t:
        return True
    for frag in ("法学院", "外国语", "外语", "外文", "法学", "法律", "英语学院", "翻译"):
        if frag in college and frag in t:
            return True
    # 学院简称（去掉「学院/系」后至少 2 字）须在标题中出现
    short = college.replace("学院", "").replace("系", "").replace("部", "")
    if len(short) >= 2 and short in t:
        return True
    return False


async def _fetch_list(client: httpx.AsyncClient, base: str) -> str | None:
    for path in LIST_PATHS:
        url = base.rstrip("/") + path
        try:
            resp = await client.get(
                url,
                headers=browser_headers(url, referer=site_root(url)),
                follow_redirects=True,
                timeout=_GET_TIMEOUT,
            )
            if resp.status_code == 200 and len(resp.text or "") > 300:
                return resp.text
        except Exception:
            continue
    return None


async def discover_from_grad_portals(
    target,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
) -> list[ParsedAnnouncement]:
    root = UNIVERSITY_ROOT_DOMAINS.get(target.university, "")
    hosts = grad_domain_candidates(target.university, root)
    results: list[ParsedAnnouncement] = []

    for host in hosts[:5]:
        base = f"https://{host}"
        if not await head_reachable(base, client):
            continue
        html = await _fetch_list(client, base)
        if not html:
            continue
        items = parse_news_list(html, base, target.college_type, board=board, phase=phase)
        for item in items[:15]:
            if not is_relevant_for_crawl(item.title, board, phase):
                continue
            if not _college_in_title(item.title, target.college, target.university):
                continue
            item.university = target.university
            item.college = target.college
            item.college_type = target.college_type
            item.source = OFFICIAL_LABEL
            results.append(item)
            if len(results) >= settings.official_list_max_items:
                return results
    return results
