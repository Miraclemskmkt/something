"""轻量级聚焦爬虫：深度 2、请求数 ≤5、受 CollegeBudget 约束。"""

from __future__ import annotations



import asyncio

import logging

import time

from urllib.parse import urljoin, urlparse



import httpx

from bs4 import BeautifulSoup



from config import settings

from crawler.anti_crawl import browser_headers, site_root

from crawler.boards import is_relevant_for_crawl

from crawler.detail_enricher import enrich_announcement

from crawler.parser import ParsedAnnouncement, parse_news_list
from crawler.source_labels import OFFICIAL_LABEL

from crawler.request_budget import CollegeBudget



logger = logging.getLogger(__name__)



FOLLOW_HINTS = ("通知", "公告", "招生", "夏令营", "推免", "预推免", "开放日", "tzgg", "xygg", "yjs")

CONTENT_RE = __import__("re").compile(r"夏令营|预推免|推免|开放日|优秀大学生|暑期营")

_FOCUS_READ = min(settings.http_read_timeout, 3.0)



_GET_TIMEOUT = httpx.Timeout(

    connect=settings.http_connect_timeout,

    read=_FOCUS_READ,

    write=_FOCUS_READ,

    pool=settings.http_connect_timeout,

)





def _same_host(url: str, host: str) -> bool:

    return urlparse(url).netloc.replace("www.", "") == host





def _notice_links(html: str, base: str, host: str, limit: int = 6) -> list[str]:

    soup = BeautifulSoup(html, "lxml")

    scored: list[tuple[int, str]] = []

    for a in soup.find_all("a", href=True):

        label = (a.get_text(strip=True) or "") + " " + a["href"]

        if not any(h in label for h in FOLLOW_HINTS):

            continue

        url = urljoin(base, a["href"])

        if not _same_host(url, host):

            continue

        score = sum(1 for h in FOLLOW_HINTS if h in label)

        scored.append((score, url))

    scored.sort(key=lambda x: -x[0])

    out: list[str] = []

    seen: set[str] = set()

    for _, url in scored:

        if url in seen:

            continue

        seen.add(url)

        out.append(url)

        if len(out) >= limit:

            break

    return out





async def _fetch_page(

    url: str,

    client: httpx.AsyncClient,

    budget: CollegeBudget | None,

) -> tuple[str, str] | None:

    if budget and not budget.can_request():

        return None

    if budget:

        budget.consume()

    try:

        resp = await client.get(

            url,

            headers=browser_headers(url, referer=site_root(url)),

            follow_redirects=True,

            timeout=_GET_TIMEOUT,

        )

        if resp.status_code != 200:

            return None

        html = resp.text or ""

        if len(html) < 200:

            return None

        return url, html

    except Exception:

        return None





def _collect_from_html(

    url: str,

    html: str,

    *,

    start: str,

    target,

    board: str,

    phase: str,

    list_pages: list[str],

    detail_pages: list[tuple[str, str]],

) -> None:

    text_sample = html[:8000]

    items = parse_news_list(html, start, target.college_type, board=board, phase=phase)

    if items:

        list_pages.append(html)



    if CONTENT_RE.search(text_sample) and any(

        k in text_sample for k in ("报名", "申请", "截止", "开营")

    ):

        detail_pages.append((url, html))





async def focus_crawl_college(

    target,

    board: str,

    phase: str,

    client: httpx.AsyncClient,

    *,

    budget: CollegeBudget | None = None,

    max_sec: float | None = None,

    max_depth: int | None = None,

) -> list[ParsedAnnouncement]:

    if not target.base_url:

        return []



    start = target.base_url.rstrip("/")

    host = urlparse(start).netloc.replace("www.", "")

    if not host:

        return []



    cap_sec = max_sec if max_sec is not None else settings.focus_crawler_max_sec

    if budget:

        cap_sec = min(cap_sec, budget.remaining_sec())

    deadline = time.monotonic() + max(1.0, cap_sec)

    max_req = settings.focus_crawler_max_requests

    max_depth = max_depth if max_depth is not None else settings.focus_crawler_depth

    inner_parallel = min(3, max_req - 1)



    list_pages: list[str] = []

    detail_pages: list[tuple[str, str]] = []

    visited: set[str] = set()

    req_count = 0



    home = await _fetch_page(start, client, budget)

    if not home or time.monotonic() >= deadline:

        return []

    req_count += 1

    home_url, home_html = home

    visited.add(home_url)

    _collect_from_html(

        home_url, home_html,

        start=start, target=target, board=board, phase=phase,

        list_pages=list_pages, detail_pages=detail_pages,

    )



    if list_pages and any(

        is_relevant_for_crawl(x.title, board, phase)

        for x in parse_news_list(list_pages[0], start, target.college_type, board=board, phase=phase)

    ):

        pass  # 已有列表页，跳过深度抓取

    elif max_depth >= 1 and req_count < max_req and time.monotonic() < deadline:

        child_urls = [

            u for u in _notice_links(home_html, start, host, limit=inner_parallel + 2)

            if u not in visited

        ][:inner_parallel]

        if child_urls and budget and budget.can_request():

            tasks = [_fetch_page(u, client, budget) for u in child_urls]

            pages = await asyncio.gather(*tasks)

            req_count += sum(1 for p in pages if p)

            for page in pages:

                if not page or time.monotonic() >= deadline:

                    continue

                url, html = page

                visited.add(url)

                _collect_from_html(

                    url, html,

                    start=start, target=target, board=board, phase=phase,

                    list_pages=list_pages, detail_pages=detail_pages,

                )

                if list_pages:

                    break



    results: list[ParsedAnnouncement] = []

    seen_url: set[str] = set()



    for html in list_pages[:1]:

        for item in parse_news_list(html, start, target.college_type, board=board, phase=phase):

            if item.url in seen_url:

                continue

            if not is_relevant_for_crawl(item.title, board, phase):

                continue

            item.university = target.university

            item.college = target.college

            item.college_type = target.college_type

            item.source = OFFICIAL_LABEL

            seen_url.add(item.url)

            results.append(item)



    for url, html in detail_pages[:2]:

        if url in seen_url:

            continue

        soup = BeautifulSoup(html, "lxml")

        h = soup.find(["h1", "h2", "title"])

        title = h.get_text(strip=True)[:500] if h else ""

        if not title or not is_relevant_for_crawl(title, board, phase):

            continue

        results.append(ParsedAnnouncement(

            title=title, url=url,

            university=target.university,

            college=target.college,

            college_type=target.college_type,

            source=OFFICIAL_LABEL,

        ))

        seen_url.add(url)



    cap = settings.official_list_max_items

    for it in results[:cap]:

        if time.monotonic() >= deadline:

            break

        try:

            await asyncio.wait_for(

                enrich_announcement(it, board=board, phase=phase, client=client, mode="light"),

                timeout=settings.enrich_timeout_light_sec,

            )

        except Exception:

            pass



    return results[:cap]

