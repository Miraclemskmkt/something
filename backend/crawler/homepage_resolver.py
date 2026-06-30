"""从学校主站反向查找学院真实子站链接。"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config import settings
from crawler.anti_crawl import browser_headers, site_root
from crawler.domain_fixer import head_reachable, probe_host
from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS

logger = logging.getLogger(__name__)

LAW_HINTS = ("法学院", "法学", "法律", "政法", "国际法", "凯原", "光华法学")
FOREIGN_HINTS = ("外国语", "外语", "外文", "英语学院", "翻译学院", "语言文化", "外文学院")
ORG_HINTS = ("学院设置", "组织机构", "院系设置", "教学单位", "学部院系")

_GET_TIMEOUT = httpx.Timeout(
    connect=min(settings.http_connect_timeout, 4.0),
    read=min(settings.http_read_timeout, 4.0),
    write=4.0,
    pool=2.0,
)


def _main_site_url(university: str) -> str | None:
    from double_first_class import DOUBLE_FIRST_CLASS_UNIVERSITIES

    for u in DOUBLE_FIRST_CLASS_UNIVERSITIES:
        if u.name == university:
            return u.url.rstrip("/")
    root = UNIVERSITY_ROOT_DOMAINS.get(university)
    if root:
        return f"https://www.{root}"
    return None


def _college_hints(college: str, college_type: str) -> tuple[str, ...]:
    hints: list[str] = [college]
    if college_type == "law":
        hints.extend(LAW_HINTS)
    else:
        hints.extend(FOREIGN_HINTS)
    short = re.sub(r"(学院|系|部)$", "", college)
    if len(short) >= 2:
        hints.append(short)
    return tuple(dict.fromkeys(hints))


def _score_link(text: str, href: str, hints: tuple[str, ...]) -> int:
    combined = f"{text} {href}"
    return sum(2 if h in text else (1 if h in combined else 0) for h in hints)


async def _fetch_html(url: str, client: httpx.AsyncClient, *, mobile: bool = False) -> str:
    try:
        resp = await client.get(
            url,
            headers=browser_headers(url, referer=site_root(url), mobile=mobile),
            follow_redirects=True,
            timeout=_GET_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.text or ""
    except Exception:
        pass
    return ""


def _extract_college_links(html: str, base_url: str, hints: tuple[str, ...]) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    scored: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text(strip=True) or "")[:60]
        href = a["href"]
        if not href or href.startswith("#"):
            continue
        score = _score_link(text, href, hints)
        if score <= 0:
            continue
        url = urljoin(base_url, href)
        if "edu.cn" not in urlparse(url).netloc:
            continue
        scored.append((score, url.rstrip("/")))
    scored.sort(key=lambda x: -x[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, u in scored:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:8]


def _is_college_subdomain(url: str, university: str) -> bool:
    """拒绝仅指向学校根域 / 新闻首页的链接。"""
    from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS

    host = urlparse(url).netloc.lower().replace("www.", "")
    root = UNIVERSITY_ROOT_DOMAINS.get(university, "")
    if not root:
        return True
    if host == root or host == f"www.{root}":
        return False
    if host.startswith(("news.", "www.news.")) and root in host:
        return False
    prefix = host.split(".")[0]
    return len(prefix) >= 2 and prefix not in ("www", "news", "english")


async def resolve_from_homepage(
    university: str,
    college: str,
    college_type: str,
    client: httpx.AsyncClient,
) -> str | None:
    """从大学官网首页匹配学院链接并 HEAD 验证。"""
    main = _main_site_url(university)
    if not main:
        return None

    hints = _college_hints(college, college_type)
    for mobile in (False, True):
        html = await _fetch_html(main, client, mobile=mobile)
        if not html:
            continue
        candidates = _extract_college_links(html, main, hints)
        if not candidates and any(h in html for h in ORG_HINTS):
            for org in _extract_college_links(html, main, ORG_HINTS + hints):
                sub = await _fetch_html(org, client, mobile=mobile)
                if sub:
                    candidates.extend(_extract_college_links(sub, org, hints))
        for url in candidates[:6]:
            host = urlparse(url).netloc
            if not host:
                continue
            found = await probe_host(host.replace("www.", ""), client)
            if found and _is_college_subdomain(found, university):
                return found
    return None
