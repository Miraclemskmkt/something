"""论坛新通知雷达：官方链接直抓 + 定向搜索触发。"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from config import settings
from crawler.boards import SUMMER_CAMP, is_relevant_for_crawl
from crawler.detail_enricher import enrich_announcement
from crawler.parser import (
    ParsedAnnouncement,
    detect_event_type,
    detect_status,
    enrich_dates,
    is_year_eligible,
)
from crawler.source_labels import FORUM_LABEL, OFFICIAL_LABEL, WECHAT_LABEL
from crawler.wechat_article import is_weixin_url

logger = logging.getLogger(__name__)

FORUM_PROVENANCE = "[信源：保研论坛→官方原文]"
FORUM_NEEDS_LINK = "[需补充官方链接]"

_NOTICE_TITLE_HINTS = ("报名通知", "招生通知", "招生简章", "夏令营通知", "推免", "预推免", "开放日", "接收推荐")


def url_dedup_key(url: str) -> str:
    p = urlparse((url or "").split("#")[0])
    return f"{p.netloc.lower()}{p.path.rstrip('/')}"


def load_known_url_keys(db: Session) -> set[str]:
    from models import Announcement

    keys: set[str] = set()
    for (url,) in db.query(Announcement.url).all():
        if url:
            keys.add(url_dedup_key(url))
    return keys


def is_url_known(url: str, known: set[str]) -> bool:
    return url_dedup_key(url) in known


def title_looks_like_notice(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    if any(k in t for k in ("求问", "请问", "经验", "蹲", "汇总")):
        return False
    return any(k in t for k in _NOTICE_TITLE_HINTS) or (
        any(k in t for k in ("夏令营", "预推免", "推免", "开放日")) and len(t) >= 12
    )


def append_provenance(summary: str | None, tag: str) -> str:
    base = (summary or "").strip()
    if tag in base:
        return base
    return f"{tag}\n{base}".strip() if base else tag


def _map_source_label(label: str) -> str:
    if label == "学院官网":
        return OFFICIAL_LABEL
    if label == "微信公众号":
        return WECHAT_LABEL
    return FORUM_LABEL


async def ingest_from_official_url(
    *,
    url: str,
    target,
    title: str,
    thread_url: str,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    fallback_text: str = "",
) -> ParsedAnnouncement | None:
    """从官方/微信 URL 抓取全文、LLM 补全，带来源溯源标记。"""
    item = ParsedAnnouncement(
        title=title or "",
        url=url,
        original_url=thread_url,
        university=target.university,
        college=target.college,
        college_type=target.college_type,
        source=WECHAT_LABEL if is_weixin_url(url) else OFFICIAL_LABEL,
        event_type=detect_event_type(title or ""),
    )
    if fallback_text:
        item.summary = fallback_text[:500]

    try:
        await enrich_announcement(
            item, board=board, phase=phase, client=client, mode="full",
        )
    except Exception as e:
        logger.debug("论坛雷达官方抓取失败 %s: %s", url[:60], e)
        if not fallback_text:
            return None
        pub, ddl, evt, fmt = enrich_dates(title, fallback_text)
        item.publish_date, item.deadline = pub, ddl
        item.event_time, item.event_format = evt, fmt

    if getattr(item, "llm_rejected", False):
        return None
    if not is_relevant_for_crawl(item.title or title, board, phase, summary=item.summary or ""):
        return None
    if not is_year_eligible(item.title or title, item.publish_date, item.deadline):
        return None

    item.title = item.title or title
    item.status = detect_status(item.title, item.deadline)
    item.summary = append_provenance(item.summary, FORUM_PROVENANCE)
    item.forum_incomplete = False
    return item


async def quick_targeted_search(
    target,
    *,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    timeout_sec: float = 5.0,
) -> ParsedAnnouncement | None:
    """针对单个学院跑一组泛搜词，超时放弃。"""
    from crawler.broad_search import build_broad_queries
    from crawler.searcher import verify_search_item, _search_engine

    queries = build_broad_queries(target, board, phase)[:1]
    if not queries:
        year = settings.min_notice_year
        queries = [f'"{target.university}" "{target.college}" 夏令营 {year}']

    query = queries[0]
    try:
        batch = await asyncio.wait_for(
            _search_engine(query, target.college_type, board, phase, client=client),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.debug("论坛雷达定向搜索超时: %s %s", target.university, target.college)
        return None
    except Exception as e:
        logger.debug("论坛雷达定向搜索失败: %s", e)
        return None

    for hit in batch[:3]:
        verified = await verify_search_item(
            hit, target, client, board=board, phase=phase,
        )
        if verified and is_year_eligible(
            verified.title, verified.publish_date, verified.deadline,
        ):
            verified.summary = append_provenance(verified.summary, FORUM_PROVENANCE)
            verified.forum_incomplete = False
            return verified
    return None


async def build_radar_item(
    *,
    title: str,
    text: str,
    links: list[str],
    thread_url: str,
    target,
    canon_url: str,
    source_label: str,
    board: str,
    phase: str,
    client: httpx.AsyncClient,
    known_urls: set[str],
) -> ParsedAnnouncement | None:
    """
    论坛帖 → 雷达处理：
    1. 有官方链且未收录 → 直抓官方原文
    2. 无官方链但像通知 → 定向搜索
    3. 否则 → 论坛帖入库（可能需补链）
    """
    has_official = canon_url and canon_url != thread_url

    if has_official and not is_url_known(canon_url, known_urls):
        item = await ingest_from_official_url(
            url=canon_url,
            target=target,
            title=title,
            thread_url=thread_url,
            board=board,
            phase=phase,
            client=client,
            fallback_text=text,
        )
        if item:
            known_urls.add(url_dedup_key(item.url))
            logger.info(
                "论坛雷达直抓官方: %s %s → %s",
                target.university, title[:40], item.url[:60],
            )
            from crawler.domain_heal import maybe_heal_college_homepage
            maybe_heal_college_homepage(
                target.university, target.college, target.college_type, item.url,
            )
            return item

    if has_official and is_url_known(canon_url, known_urls):
        logger.debug("论坛帖官方链已收录，跳过: %s", canon_url[:60])
        return None

    if title_looks_like_notice(title):
        searched = await quick_targeted_search(
            target, board=board, phase=phase, client=client,
            timeout_sec=getattr(settings, "forum_radar_search_timeout_sec", 5.0),
        )
        if searched and not is_url_known(searched.url, known_urls):
            searched.original_url = thread_url
            known_urls.add(url_dedup_key(searched.url))
            logger.info(
                "论坛雷达定向搜索命中: %s %s → %s",
                target.university, title[:40], searched.url[:60],
            )
            from crawler.domain_heal import maybe_heal_college_homepage
            maybe_heal_college_homepage(
                target.university, target.college, target.college_type, searched.url,
            )
            return searched

    if not canon_url:
        return None

    pub, ddl, evt, fmt = enrich_dates(title, text)
    if not is_year_eligible(title, pub, ddl):
        return None

    forum_incomplete = canon_url == thread_url and len(text) < 200
    summary = text[:500] if text else None
    if forum_incomplete:
        summary = append_provenance(summary, FORUM_NEEDS_LINK)

    return ParsedAnnouncement(
        title=title,
        url=canon_url,
        original_url=thread_url if canon_url != thread_url else None,
        publish_date=pub,
        deadline=ddl,
        event_time=evt,
        event_format=fmt,
        event_type=detect_event_type(title),
        status=detect_status(title, ddl),
        summary=summary,
        source=_map_source_label(source_label),
        university=target.university,
        college=target.college,
        college_type=target.college_type,
        forum_incomplete=forum_incomplete,
    )
