"""保研类网站聚合抓取：保研论坛 Discuz 板块 → 匹配法学/外语学院通知 → 入库。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config import settings
from crawler.boards import SUMMER_CAMP, is_relevant_for_crawl
from crawler.domain_discovery import record_discovered_url
from crawler.noise_filter import passes_title_filter
from crawler.source_labels import FORUM_LABEL, OFFICIAL_LABEL, WECHAT_LABEL
from crawler.parser import (
    ParsedAnnouncement,
    detect_event_type,
    detect_status,
    enrich_dates,
    is_valid_announcement_url,
    is_year_eligible,
)
from crawler.university_config import UNIVERSITY_TARGETS

logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).resolve().parent.parent / "data" / "baoyan_sites.json"
SEEN_FILE = Path(__file__).resolve().parent.parent / "data" / "baoyan_sites_seen.json"

LIST_KEYWORDS = ("夏令营", "预推免", "推免", "开放日", "暑期营")
SKIP_TITLE = ("汇总帖", "真题资料", "交流群", "经验分享", "求助", "问答", "求问", "请问", "蹲一个", "蹲一个", "有没有", "怎么", "经验")
LAW_HINTS = ("法学院", "法学", "法律", "国际法", "法硕")
FOREIGN_HINTS = ("外国语", "外语", "外文", "英语", "翻译", "语言", "高翻")
DEFAULT_KEYWORD_FILTERS = list(LAW_HINTS) + list(FOREIGN_HINTS)

EE_BAN_BASE = "https://www.eeban.com"

_EXTERNAL_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+?(?:edu\.cn|ac\.cn|mp\.weixin\.qq\.com/s/[A-Za-z0-9_-]+)[^\s\"'<>]*",
    re.I,
)
_NOTICE_PATH_HINTS = ("tzgg", "list", "notice", "news", "info", "xwtz", "xyxw", "xwzx", ".htm", ".pdf")


def _forum_year_ok(
    title: str,
    text: str = "",
    publish_date: str | None = None,
    deadline: str | None = None,
) -> bool:
    """论坛帖须符合 min_notice_year（默认 2026）：标题、正文摘要或日期中可见目标年份。"""
    title = (title or "").strip()
    snippet = (text or "").strip()[:800]
    if is_year_eligible(title, publish_date, deadline):
        return True
    if snippet:
        return is_year_eligible(f"{title} {snippet}", publish_date, deadline)
    return False


def _load_config() -> list[dict]:
    if not CONFIG_FILE.is_file():
        return []
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return [s for s in data.get("sources", []) if s.get("enabled")]
    except Exception as e:
        logger.debug("baoyan_sites config load failed: %s", e)
        return []


def _load_seen_state() -> tuple[set[str], set[str]]:
    if not SEEN_FILE.is_file():
        return set(), set()
    try:
        raw = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return set(), set()
    if isinstance(raw, list):
        return set(raw), set()
    return set(raw.get("thread_ids", [])), set(raw.get("title_keys", []))


def _save_seen_state(thread_ids: set[str], title_keys: set[str]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(
        json.dumps(
            {
                "thread_ids": sorted(thread_ids)[-3000:],
                "title_keys": sorted(title_keys)[-2000:],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _thread_id(url: str) -> str:
    m = re.search(r"thread-(\d+)", url or "")
    return m.group(1) if m else url


def _title_key(university: str, college: str, title: str) -> str:
    t = re.sub(r"20\d{2}年?", "", title or "")
    t = re.sub(r"\s+", "", t)[:48]
    return f"{university}|{college}|{t}"


def _is_duplicate_title(title_key: str, title_keys: set[str], threshold: float = 0.88) -> bool:
    if title_key in title_keys:
        return True
    for existing in title_keys:
        if existing.split("|")[:2] != title_key.split("|")[:2]:
            continue
        if SequenceMatcher(None, existing.split("|")[-1], title_key.split("|")[-1]).ratio() >= threshold:
            return True
    return False


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href.split("#")[0].strip()
    return urljoin(EE_BAN_BASE + "/", href.lstrip("/")).split("#")[0].strip()


def _score_notice_url(url: str) -> int:
    lower = (url or "").lower()
    score = 0
    if "mp.weixin.qq.com" in lower:
        score += 6
    if any(h in lower for h in _NOTICE_PATH_HINTS):
        score += 10
    if any(h in lower for h in ("yz.", "graduate", "zsxx", "zs.")):
        score += 4
    if lower.count("/") <= 3:
        score -= 3
    return score


def _extract_external_links(html: str) -> list[str]:
    """从整页 HTML（含隐藏/未渲染片段）提取 edu.cn / 微信链接。"""
    found: set[str] = set()
    for m in _EXTERNAL_URL_RE.finditer(html or ""):
        u = m.group(0).rstrip(".,;)]}\"'")
        if u:
            found.add(u)
    soup = BeautifulSoup(html or "", "lxml")
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if any(k in h for k in ("mp.weixin.qq.com", "edu.cn", "ac.cn")):
            found.add(_abs_url(h))
    return sorted(found, key=_score_notice_url, reverse=True)


def _pick_notice_url(links: list[str], thread_url: str) -> tuple[str, str]:
    edu: list[str] = []
    wx: list[str] = []
    for raw in links:
        u = _abs_url(raw)
        if not u:
            continue
        if "mp.weixin.qq.com" in u and "/s" in u:
            wx.append(u)
        elif ".edu.cn" in u or ".ac.cn" in u:
            edu.append(u)
    edu.sort(key=_score_notice_url, reverse=True)
    wx.sort(key=_score_notice_url, reverse=True)
    if edu:
        return edu[0], "学院官网"
    if wx:
        return wx[0], "微信公众号"
    if "eeban.com" in thread_url and re.search(r"/thread-\d+", thread_url):
        return thread_url, "保研论坛"
    return "", ""


def _parse_eeban_thread(html: str, thread_url: str) -> tuple[str, str, list[str]]:
    soup = BeautifulSoup(html or "", "lxml")
    title_el = soup.select_one("#thread_subject") or soup.select_one("h1")
    title = title_el.get_text(strip=True) if title_el else ""
    body = soup.select_one(".t_f") or soup.select_one(".pcb")
    text = body.get_text("\n", strip=True) if body else ""
    links = _extract_external_links(html)
    return title[:500], text[:25000], links


def _list_passes(title: str, src: dict) -> bool:
    if not title or len(title) < 8:
        return False
    if any(k in title for k in SKIP_TITLE):
        return False
    if not any(k in title for k in LIST_KEYWORDS):
        return False

    mode = src.get("mode", "subject")
    filters = src.get("keyword_filters") or DEFAULT_KEYWORD_FILTERS

    if mode == "university":
        matched = any(k in title for k in filters)
    elif not any(h in title for h in LAW_HINTS + FOREIGN_HINTS):
        return False
    elif filters:
        matched = any(k in title for k in filters)
    else:
        matched = True

    return matched and _forum_year_ok(title)


def _headers() -> dict[str, str]:
    return {"User-Agent": settings.user_agent, "Accept-Language": "zh-CN,zh;q=0.9"}


def _match_target(title: str, summary: str):
    combined = f"{title} {summary}"
    if not any(k in combined for k in ("夏令营", "开放日", "推免", "预推免")):
        return None

    best = None
    best_score = 0
    for t in UNIVERSITY_TARGETS:
        if t.university not in combined:
            continue
        score = 5
        if t.college in combined:
            score += 8
        elif t.college[:2] in combined and len(t.college) >= 4:
            score += 3
        if t.college_type == "law" and any(h in combined for h in LAW_HINTS):
            score += 4
        if t.college_type == "foreign_lang" and any(h in combined for h in FOREIGN_HINTS):
            score += 4
        if "法学院" in title and t.college_type == "foreign_lang":
            score -= 6
        if "外国语" in title and t.college_type == "law":
            score -= 6
        if score > best_score:
            best_score = score
            best = t
    return best if best_score >= 9 else None


async def _fetch_eeban_forum_list(
    src: dict,
    *,
    client: httpx.AsyncClient,
) -> list[tuple[str, str]]:
    forum_id = int(src.get("forum_id") or 0)
    pages = int(src.get("pages") or settings.baoyan_sites_list_pages)
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        url = f"{EE_BAN_BASE}/forum-{forum_id}-{page}.html"
        try:
            resp = await client.get(url, timeout=20, follow_redirects=True)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.select("a.s.xst"):
                title = a.get_text(strip=True)
                href = _abs_url(a.get("href") or "")
                if not href or href in seen:
                    continue
                seen.add(href)
                if _list_passes(title, src):
                    rows.append((title, href))
        except Exception as e:
            logger.warning("保研论坛列表失败 forum=%s page=%s: %s", forum_id, page, e)
        await asyncio.sleep(settings.baoyan_sites_delay)
    return rows


async def _fetch_eeban_thread(
    thread_url: str,
    *,
    client: httpx.AsyncClient,
) -> tuple[str, str, list[str]]:
    try:
        resp = await client.get(thread_url, timeout=25, follow_redirects=True, headers=_headers())
        if resp.status_code != 200:
            return "", "", []
        return _parse_eeban_thread(resp.text, thread_url)
    except Exception as e:
        logger.debug("保研论坛帖子失败 %s: %s", thread_url[:60], e)
        return "", "", []


def _map_forum_source(source_label: str) -> str:
    if source_label == "学院官网":
        return OFFICIAL_LABEL
    if source_label == "微信公众号":
        return WECHAT_LABEL
    return FORUM_LABEL


async def crawl_baoyan_sites(
    board: str = SUMMER_CAMP,
    phase: str = "notice",
    db=None,
) -> list[ParsedAnnouncement]:
    if not settings.baoyan_sites_enabled:
        return []

    sources = _load_config()
    if not sources:
        logger.info("保研网站：无已启用数据源（请配置 data/baoyan_sites.json）")
        return []

    seen_tids, seen_titles = _load_seen_state()
    known_urls: set[str] = set()
    if db is not None:
        from crawler.forum_radar import load_known_url_keys
        known_urls = load_known_url_keys(db)

    candidates: list[tuple[str, str, str]] = []

    async with httpx.AsyncClient(timeout=settings.request_timeout, headers=_headers()) as client:
        for src in sources:
            if src.get("type") != "eeban_forum":
                continue
            name = src.get("name") or f"forum-{src.get('forum_id')}"
            rows = await _fetch_eeban_forum_list(src, client=client)
            logger.info("保研论坛 [%s] 候选 %d 条", name, len(rows))
            for title, url in rows:
                tid = _thread_id(url)
                if tid in seen_tids:
                    continue
                candidates.append((title, url, name))

        results: list[ParsedAnnouncement] = []
        for list_title, thread_url, src_name in candidates[: settings.baoyan_sites_max_threads]:
            if not _forum_year_ok(list_title):
                logger.debug(
                    "论坛列表帖年份不符(%d)，跳过: %s",
                    settings.min_notice_year,
                    list_title[:60],
                )
                seen_tids.add(_thread_id(thread_url))
                continue
            await asyncio.sleep(settings.baoyan_sites_delay)
            title, text, links = await _fetch_eeban_thread(thread_url, client=client)
            title = title or list_title

            from crawler.llm_classifier import classify_forum_post
            if settings.llm_enabled and settings.llm_forum_classify_enabled:
                clf = classify_forum_post(title, text[:300], url=thread_url)
                if not clf.relevant and clf.failure_type in ("success", "ambiguous"):
                    seen_tids.add(_thread_id(thread_url))
                    continue

            from crawler.llm_link_extractor import extract_notice_urls
            has_official = any(
                "edu.cn" in l or "ac.cn" in l or "mp.weixin.qq.com" in l for l in links
            )
            if settings.llm_enabled and settings.llm_link_extract_enabled and not has_official:
                llm_links = extract_notice_urls(text, title=title)
                if llm_links:
                    links = list(dict.fromkeys(links + llm_links))

            if not passes_title_filter(title, text[:500]):
                continue
            if not is_relevant_for_crawl(title, board, phase, summary=text[:500]):
                continue

            target = _match_target(title, text)
            if not target:
                continue

            tkey = _title_key(target.university, target.college, title)
            if _is_duplicate_title(tkey, seen_titles):
                seen_tids.add(_thread_id(thread_url))
                continue

            canon_url, source_label = _pick_notice_url(links, thread_url)
            if not canon_url:
                continue
            if not text and title:
                text = title

            pub, ddl, evt, fmt = enrich_dates(title, text)
            if not _forum_year_ok(title, text, pub, ddl):
                logger.debug(
                    "论坛帖年份不符(%d)，丢弃: %s",
                    settings.min_notice_year,
                    (title or list_title)[:60],
                )
                seen_tids.add(_thread_id(thread_url))
                continue

            if source_label == "学院官网":
                record_discovered_url(
                    university=target.university,
                    college=target.college,
                    college_type=target.college_type,
                    url=canon_url,
                )

            from crawler.forum_radar import build_radar_item

            item = await build_radar_item(
                title=title,
                text=text,
                links=links,
                thread_url=thread_url,
                target=target,
                canon_url=canon_url,
                source_label=source_label,
                board=board,
                phase=phase,
                client=client,
                known_urls=known_urls,
            )
            if not item:
                seen_tids.add(_thread_id(thread_url))
                continue

            results.append(item)
            seen_tids.add(_thread_id(thread_url))
            seen_titles.add(tkey)

    if results:
        _save_seen_state(seen_tids, seen_titles)
    logger.info("保研网站匹配 %d 条法学/外语通知", len(results))
    return results


async def enrich_baoyan_items(
    items: list[ParsedAnnouncement],
    *,
    client: httpx.AsyncClient,
    board: str = SUMMER_CAMP,
    phase: str = "notice",
) -> list[ParsedAnnouncement]:
    from crawler.detail_enricher import enrich_announcement
    from crawler.field_enricher import enrich_parsed_item
    from crawler.forum_radar import FORUM_PROVENANCE
    from crawler.wechat import enrich_wechat_items
    from crawler.wechat_article import is_weixin_url

    out: list[ParsedAnnouncement] = []
    wx_items: list[ParsedAnnouncement] = []

    for item in items:
        if is_weixin_url(item.url):
            wx_items.append(item)
            continue
        if getattr(item, "forum_incomplete", False) or "eeban.com" in (item.url or ""):
            await enrich_parsed_item(item, client=client, force_llm=bool(item.summary))
            if not item.llm_rejected:
                out.append(item)
            continue
        if FORUM_PROVENANCE in (item.summary or ""):
            out.append(item)
            continue
        await enrich_announcement(item, board=board, phase=phase, client=client, mode="full")
        if not getattr(item, "llm_rejected", False):
            out.append(item)

    if wx_items:
        wx_done = await enrich_wechat_items(wx_items, client=client, board=board, phase=phase)
        out.extend(wx_done)

    return [i for i in out if not getattr(i, "llm_rejected", False)]
