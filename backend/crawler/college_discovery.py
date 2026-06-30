"""从官网发现法学院/外国语学院通知页，并探测可达性。"""

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config import settings
from crawler.fetcher import create_http_client

LAW_HINTS = ("法学院", "法学", "法律", "政法", "国际法", "凯原法学", "光华法学", "王健法学")
FOREIGN_HINTS = ("外国语", "外语", "外文", "英语学院", "翻译", "语言文化")
NOTICE_HINTS = ("通知", "公告", "新闻", "tzgg", "list.htm", "xyxw")
SKIP_DOMAINS = ("baidu.com", "weixin.qq.com", "gov.cn")

# 常见通知页路径模板（基于主域名推导）
LAW_PATH_GUESSES = [
    "/xwzx/tzgg/index.htm",
    "/882/list.htm",
    "/tzgg/list.htm",
    "/Data/List/tzgg",
]
FOREIGN_PATH_GUESSES = LAW_PATH_GUESSES


@dataclass
class CollegeProbeResult:
    university: str
    main_url: str
    college_type: str
    college_name: str = ""
    news_urls: list[str] = field(default_factory=list)
    main_ok: bool = False
    main_status: int = 0
    college_found: bool = False
    notice_ok: bool = False
    error: str = ""


def _domain_from_url(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def _guess_college_hosts(main_url: str) -> list[str]:
    parsed = urlparse(main_url)
    base_domain = parsed.netloc.replace("www.", "")
    parts = base_domain.split(".")
    if len(parts) >= 3 and parts[-2] == "edu" and parts[-1] in ("cn", "com"):
        suffix = ".".join(parts[-3:])
    elif len(parts) >= 2:
        suffix = ".".join(parts[-2:])
    else:
        suffix = base_domain
    return [f"law.{suffix}", f"sfl.{suffix}", f"fl.{suffix}", f"wy.{suffix}", f"sis.{suffix}"]


async def fetch(client: httpx.AsyncClient, url: str) -> tuple[int, str]:
    try:
        resp = await client.get(url)
        text = resp.text if resp.status_code == 200 else ""
        return resp.status_code, text
    except Exception as e:
        return -1, str(e)


def _find_college_links(html: str, base_url: str, hints: tuple[str, ...]) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if not any(h in text for h in hints):
            continue
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if any(d in parsed.netloc for d in SKIP_DOMAINS):
            continue
        if "edu.cn" not in parsed.netloc and "edu.com" not in parsed.netloc:
            continue
        if url not in seen:
            seen.add(url)
            found.append((text[:40], url))
    return found[:5]


def _find_notice_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        combined = text + href
        if not any(h in combined for h in NOTICE_HINTS):
            continue
        url = urljoin(base_url, href)
        if url not in seen and "edu.cn" in url:
            seen.add(url)
            urls.append(url)
    return urls[:3]


async def probe_university(
    client: httpx.AsyncClient,
    name: str,
    main_url: str,
    college_type: str,
) -> CollegeProbeResult:
    hints = LAW_HINTS if college_type == "law" else FOREIGN_HINTS
    result = CollegeProbeResult(university=name, main_url=main_url, college_type=college_type)

    status, html = await fetch(client, main_url)
    result.main_status = status
    result.main_ok = status == 200

    if not result.main_ok:
        result.error = f"官网不可达({status})"
        return result

    links = _find_college_links(html, main_url, hints)
    college_base = ""
    if links:
        result.college_found = True
        result.college_name = links[0][0]
        college_base = links[0][1]
    else:
        for host in _guess_college_hosts(main_url):
            guess = f"https://{host}/"
            st, _ = await fetch(client, guess)
            if st == 200:
                college_base = guess
                result.college_found = True
                result.college_name = "法学院" if college_type == "law" else "外国语学院"
                break

    if not college_base:
        result.error = "未找到学院入口"
        return result

    st2, college_html = await fetch(client, college_base)
    notice_urls = _find_notice_links(college_html, college_base) if st2 == 200 else []

    if not notice_urls:
        guesses = LAW_PATH_GUESSES if college_type == "law" else FOREIGN_PATH_GUESSES
        base = college_base.rstrip("/")
        for g in guesses:
            u = base + g if g.startswith("/") else f"{base}/{g}"
            st3, _ = await fetch(client, u)
            if st3 == 200:
                notice_urls.append(u)
                break

    result.news_urls = notice_urls
    result.notice_ok = len(notice_urls) > 0
    if not result.notice_ok:
        result.error = "学院可达但未定位通知页"
    return result


async def probe_all(universities, college_types=("law", "foreign_lang"), limit: int | None = None):
    sem = asyncio.Semaphore(6)
    targets = universities[:limit] if limit else universities
    results: list[CollegeProbeResult] = []

    async with create_http_client(timeout=12) as client:
        async def one(uni, ct):
            async with sem:
                return await probe_university(client, uni.name, uni.url, ct)

        tasks = []
        for uni in targets:
            for ct in college_types:
                tasks.append(one(uni, ct))
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for r in done:
            if isinstance(r, CollegeProbeResult):
                results.append(r)
    return results
