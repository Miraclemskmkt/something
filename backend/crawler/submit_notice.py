"""用户提交通知链接：官方校验 → 抓取解析 → 入库。"""
import logging
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from crawler.boards import PRE_ADMISSION, SUMMER_CAMP, is_relevant_for_crawl
from crawler.coverage import sync_coverage_from_announcements
from crawler.detail_enricher import enrich_announcement
from crawler.fetcher import fetch_page, is_blocked_html
from crawler.official_verify import classify_source, is_official_host, resolve_final_url, verify_official_url
from crawler.parser import (
    ParsedAnnouncement,
    detect_event_type,
    detect_status,
    enrich_from_html,
    is_valid_announcement_url,
    is_year_eligible,
)
from crawler.service import save_announcements
from crawler.university_config import UNIVERSITY_TARGETS
from crawler.url_quality import is_recap_notice, is_stale_url
from models import Announcement

logger = logging.getLogger(__name__)


class SubmitNoticeError(Exception):
    """用户可读的提交失败原因。"""

    def __init__(self, message: str, *, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def list_submit_targets() -> list[dict]:
    """供前端下拉选择的学院列表。"""
    rows = []
    seen: set[tuple[str, str]] = set()
    for t in UNIVERSITY_TARGETS:
        key = (t.university, t.college)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "university": t.university,
            "college": t.college,
            "college_type": t.college_type,
            "label": f"{t.university} - {t.college}",
        })
    rows.sort(key=lambda x: x["label"])
    return rows


def find_target(university: str, college: str):
    for t in UNIVERSITY_TARGETS:
        if t.university == university and t.college == college:
            return t
    return None


def extract_page_title(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[str] = []
    for sel in ("h1", "h2", "h3", ".article h3", ".title", ".news-title"):
        for el in soup.select(sel):
            t = el.get_text(strip=True)
            if len(t) >= 6:
                candidates.append(t[:500])
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        candidates.append(og["content"].strip()[:500])
    if soup.title and soup.title.string:
        t = soup.title.string.strip()
        for sep in ("-", "|", "_", "—"):
            if sep in t:
                t = t.split(sep)[0].strip()
        if len(t) >= 6:
            candidates.append(t[:500])
    if not candidates:
        return ""
    camp = [c for c in candidates if any(k in c for k in ("夏令营", "预推免", "推免", "开放日"))]
    pool = camp or candidates
    return max(pool, key=len)


def _verify_submitted_url(
    url: str,
    target,
    *,
    title: str = "",
    html: str | None = None,
) -> tuple[bool, str]:
    parsed = urlparse(url)
    if not is_official_host(parsed.netloc):
        return False, ""

    ok, source = verify_official_url(url, title, target, None)
    if ok:
        return True, source

    if html and title:
        ok, source = verify_official_url(url, title, target, None)
        if ok:
            return True, source

    if is_official_host(parsed.netloc) and target.base_url:
        college_host = urlparse(target.base_url).netloc.lower().replace("www.", "")
        current = parsed.netloc.lower().replace("www.", "")
        if current == college_host or current.endswith("." + college_host):
            return True, classify_source(url, target)

    return False, ""


async def submit_notice_link(
    *,
    url: str,
    university: str,
    college: str,
    board: str,
    db: Session,
) -> tuple[Announcement, bool]:
    """
    校验官方链接、提取字段并入库。
    返回 (通知记录, 是否新建)。
    """
    url = (url or "").strip()
    university = (university or "").strip()
    college = (college or "").strip()

    if not url:
        raise SubmitNoticeError("请填写通知链接")
    if not university or not college:
        raise SubmitNoticeError("请选择学校与学院")
    if board not in (SUMMER_CAMP, PRE_ADMISSION):
        board = SUMMER_CAMP

    target = find_target(university, college)
    if not target:
        raise SubmitNoticeError("该学院不在本平台收录范围内", code="not_monitored")

    if not is_valid_announcement_url(url):
        raise SubmitNoticeError("链接格式无效，请填写 http(s) 开头的 edu.cn 官方地址")

    headers = {"User-Agent": "Mozilla/5.0 (compatible; CampSubmit/1.0)"}
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers=headers,
        verify=False,
    ) as client:
        final_url = await resolve_final_url(url, client)
        if not is_valid_announcement_url(final_url):
            raise SubmitNoticeError("跳转后的链接无效")

        html = await fetch_page(final_url, client=client)
        title = extract_page_title(html) if html and not is_blocked_html(html) else ""

        ok, source_label = _verify_submitted_url(
            final_url, target, title=title, html=html,
        )
        if not ok:
            raise SubmitNoticeError(
                "该链接未通过官方来源校验。"
                "请确认链接来自该学院或学校官网（*.edu.cn），且与所选学院一致。",
                code="unofficial",
            )

        if not title:
            title = f"{university}{college}通知"
        if is_recap_notice(title) or is_stale_url(final_url):
            raise SubmitNoticeError("该链接似为活动回顾或过期通知，无法收录", code="stale")

        phase = "notice"
        if not is_relevant_for_crawl(title, board, phase):
            label = "夏令营" if board == SUMMER_CAMP else "预推免"
            raise SubmitNoticeError(
                f"页面标题与「{label}」通知不符，请确认链接是否正确",
                code="irrelevant",
            )

        item = ParsedAnnouncement(
            title=title,
            url=final_url,
            university=target.university,
            college=target.college,
            college_type=target.college_type,
            source=source_label or "学院官网",
            event_type=detect_event_type(title),
        )

        if html and not is_blocked_html(html):
            enrich_from_html(item, html)

        board_use = PRE_ADMISSION if "预推免" in (item.title or "") else board
        await enrich_announcement(item, board=board_use, phase=phase, client=client)

        if not is_year_eligible(item.title, item.publish_date, item.deadline):
            raise SubmitNoticeError("仅收录 2026 年及以后的夏令营/预推免通知", code="year")

        item.status = detect_status(item.title, item.deadline)

        existing = db.query(Announcement).filter(Announcement.url == item.url).first()
        is_new = existing is None

        new_count, upd_count = save_announcements(db, [item])
        sync_coverage_from_announcements(db)

        ann = db.query(Announcement).filter(Announcement.url == item.url).first()
        if not ann:
            raise SubmitNoticeError("入库失败，请稍后重试", code="save_failed")

        logger.info(
            "用户提交通知: %s %s url=%s new=%s",
            university, college, final_url, is_new,
        )
        return ann, is_new
