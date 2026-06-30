"""保研汇总公众号 RSS 拉取：被动接收、零搜狗请求。"""
from __future__ import annotations

import json
import logging
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx

from config import settings
from crawler.boards import SUMMER_CAMP, is_relevant_for_crawl
from crawler.noise_filter import passes_title_filter
from crawler.parser import (
    ParsedAnnouncement,
    detect_event_type,
    detect_status,
    enrich_dates,
    is_year_eligible,
)
from crawler.university_config import UNIVERSITY_TARGETS
from crawler.source_labels import WECHAT_LABEL
from crawler.wechat_article import is_weixin_url, weixin_fingerprint

logger = logging.getLogger(__name__)

FEEDS_FILE = Path(__file__).resolve().parent.parent / "data" / "wechat_rss_feeds.json"
SEEN_FILE = Path(__file__).resolve().parent.parent / "data" / "wechat_rss_seen.json"

LAW_HINTS = ("法学院", "法学", "法律")
FOREIGN_HINTS = ("外国语", "外语", "外文", "英语", "翻译")


def _load_feeds() -> list[dict]:
    if not FEEDS_FILE.is_file():
        return []
    try:
        data = json.loads(FEEDS_FILE.read_text(encoding="utf-8"))
        return [f for f in data.get("feeds", []) if f.get("enabled") and f.get("url")]
    except Exception as e:
        logger.debug("RSS feeds load failed: %s", e)
        return []


def _load_seen() -> set[str]:
    if not SEEN_FILE.is_file():
        return set()
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_seen(seen: set[str]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(
        json.dumps(sorted(seen)[-2000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _match_target(title: str, summary: str) -> object | None:
    combined = f"{title} {summary}"
    if not any(k in combined for k in ("夏令营", "开放日", "推免", "预推免")):
        return None

    best = None
    best_score = 0
    for t in UNIVERSITY_TARGETS:
        score = 0
        if t.university in combined:
            score += 5
        if t.college in combined or t.college[:2] in combined:
            score += 4
        if t.college_type == "law" and any(h in combined for h in LAW_HINTS):
            score += 2
        if t.college_type == "foreign_lang" and any(h in combined for h in FOREIGN_HINTS):
            score += 2
        if score > best_score:
            best_score = score
            best = t
    return best if best_score >= 7 else None


def _parse_rss(xml_text: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as e:
        logger.warning("RSS 解析失败: %s", e)
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//item") + root.findall(".//atom:entry", ns):
        title_el = item.find("title") or item.find("atom:title", ns)
        link_el = item.find("link") or item.find("atom:link", ns)
        desc_el = item.find("description") or item.find("atom:summary", ns) or item.find("content")

        title = (title_el.text or "").strip() if title_el is not None else ""
        link = ""
        if link_el is not None:
            link = link_el.get("href") or (link_el.text or "").strip()
        summary = ""
        if desc_el is not None:
            summary = (desc_el.text or "").strip()
            summary = re.sub(r"<[^>]+>", " ", summary)

        pub = item.find("pubDate")
        pub_dt = None
        if pub is not None and pub.text:
            try:
                pub_dt = parsedate_to_datetime(pub.text)
            except Exception:
                pass

        if title and link:
            items.append({"title": title, "link": link, "summary": summary, "pub": pub_dt})
    return items


async def fetch_rss_feed(url: str, *, client: httpx.AsyncClient) -> list[dict]:
    try:
        resp = await client.get(url, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return []
        return _parse_rss(resp.text)
    except Exception as e:
        logger.warning("RSS 拉取失败 %s: %s", url[:60], e)
        return []


async def crawl_wechat_rss(
    board: str = SUMMER_CAMP,
    phase: str = "notice",
) -> list[ParsedAnnouncement]:
    if not settings.wechat_rss_enabled:
        return []

    feeds = _load_feeds()
    if not feeds:
        logger.info("微信 RSS：无已启用 feed（请配置 data/wechat_rss_feeds.json）")
        return []

    seen = _load_seen()
    results: list[ParsedAnnouncement] = []
    url_seen: set[str] = set()

    async with httpx.AsyncClient(
        timeout=settings.request_timeout,
        headers={"User-Agent": settings.user_agent},
    ) as client:
        for feed in feeds:
            entries = await fetch_rss_feed(feed["url"], client=client)
            logger.info("RSS [%s] %d 条", feed.get("name", ""), len(entries))
            for ent in entries:
                title = ent["title"]
                link = ent["link"]
                summary = ent.get("summary") or ""

                if not is_weixin_url(link):
                    continue
                fp = weixin_fingerprint(link)
                if fp in seen or link in url_seen:
                    continue

                if not passes_title_filter(title, summary):
                    continue
                if not is_relevant_for_crawl(title, board, phase, summary=summary):
                    continue

                target = _match_target(title, summary)
                if not target:
                    continue

                pub, ddl, evt, fmt = enrich_dates(title, summary)
                if not is_year_eligible(title, pub, ddl):
                    continue

                item = ParsedAnnouncement(
                    title=title,
                    url=link,
                    publish_date=pub,
                    deadline=ddl,
                    event_time=evt,
                    event_format=fmt,
                    event_type=detect_event_type(title),
                    status=detect_status(title, ddl),
                    summary=summary[:500] if summary else None,
                    source=WECHAT_LABEL,
                    university=target.university,
                    college=target.college,
                    college_type=target.college_type,
                )
                results.append(item)
                url_seen.add(link)
                seen.add(fp)

    if results:
        _save_seen(seen)
    logger.info("微信 RSS 匹配 %d 条法学/外语通知", len(results))
    return results
