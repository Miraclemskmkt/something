"""域名 HEAD 探测与别名自动修正。"""
from __future__ import annotations

import asyncio
import logging

import httpx

from config import settings
from crawler.anti_crawl import browser_headers, site_root
from crawler.domain_aliases import alias_hosts
from crawler.domain_overrides import get_homepage_override, set_homepage_override

logger = logging.getLogger(__name__)

LIST_PROBE_PATH = "/xwzx/tzgg/index.htm"

_HEAD_TIMEOUT = httpx.Timeout(
    connect=min(settings.http_connect_timeout, 3.0),
    read=settings.http_head_timeout,
    write=settings.http_head_timeout,
    pool=2.0,
)


async def head_reachable(
    url: str,
    client: httpx.AsyncClient,
    *,
    mobile: bool = False,
) -> bool:
    if not url.startswith("http"):
        url = f"https://{url}"
    try:
        resp = await client.head(
            url,
            headers=browser_headers(url, referer=site_root(url), mobile=mobile),
            follow_redirects=True,
            timeout=_HEAD_TIMEOUT,
        )
        if 200 <= resp.status_code < 400:
            return True
        if resp.status_code in (403, 405):
            resp = await client.get(
                url,
                headers=browser_headers(url, referer=site_root(url), mobile=mobile),
                follow_redirects=True,
                timeout=_HEAD_TIMEOUT,
            )
            return 200 <= resp.status_code < 400 and len(resp.text or "") > 200
    except Exception:
        pass
    return False


async def probe_host(host: str, client: httpx.AsyncClient) -> str | None:
    for mobile in (False, True):
        for scheme in ("https", "http"):
            url = f"{scheme}://{host}/"
            if await head_reachable(url, client, mobile=mobile):
                return url.rstrip("/")
    return None


async def _has_notice_list(url: str, client: httpx.AsyncClient) -> bool:
    base = url.rstrip("/")
    test = base + LIST_PROBE_PATH
    try:
        resp = await client.get(
            test,
            headers=browser_headers(test, referer=site_root(test)),
            follow_redirects=True,
            timeout=_HEAD_TIMEOUT,
        )
        if resp.status_code != 200:
            return False
        text = resp.text or ""
        return len(text) > 300 and any(k in text for k in ("通知", "公告", "list", "tzgg"))
    except Exception:
        return False


async def find_working_homepage(
    current: str,
    college_type: str,
    client: httpx.AsyncClient,
    *,
    force_aliases: bool = False,
) -> tuple[str | None, str]:
    """返回 (新 homepage, 来源说明)。"""
    candidates: list[tuple[str, str]] = []

    if current and not force_aliases:
        url = current if current.startswith("http") else f"https://{current}"
        if await head_reachable(url, client) and await _has_notice_list(url, client):
            return url.rstrip("/"), "current_ok"

    if current:
        url = current if current.startswith("http") else f"https://{current}"
        if await head_reachable(url, client):
            candidates.append((url.rstrip("/"), "current_head"))

    for host in alias_hosts(current, college_type):
        found = await probe_host(host, client)
        if found:
            candidates.append((found, f"alias:{host}"))

    for url, tag in candidates:
        if await _has_notice_list(url, client):
            return url, tag

    if candidates:
        return candidates[0][0], candidates[0][1] + "_no_list"
    return None, "not_found"


async def fix_domain_for_target(
    target,
    client: httpx.AsyncClient,
    *,
    force_aliases: bool = False,
) -> str | None:
    existing = get_homepage_override(target.university, target.college, target.college_type)
    base = existing or target.base_url
    if not base:
        return None

    new_url, reason = await find_working_homepage(
        base, target.college_type, client, force_aliases=force_aliases,
    )
    if not new_url or new_url.rstrip("/") == base.rstrip("/"):
        return None

    if not set_homepage_override(
        target.university, target.college, target.college_type, new_url, source=reason,
    ):
        return None
    return new_url


async def batch_fix_domains(
    targets: list,
    client: httpx.AsyncClient,
    *,
    concurrency: int = 12,
    force_aliases: bool = False,
) -> list[tuple[str, str, str, str]]:
    """返回 [(学校, 学院, 旧域, 新域), ...]"""
    sem = asyncio.Semaphore(concurrency)
    fixed: list[tuple[str, str, str, str]] = []

    async def one(t):
        async with sem:
            old = get_homepage_override(t.university, t.college, t.college_type) or t.base_url
            new = await fix_domain_for_target(t, client, force_aliases=force_aliases)
            if new:
                fixed.append((t.university, t.college, old, new))

    await asyncio.gather(*[one(t) for t in targets])
    return fixed
