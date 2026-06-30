"""通知 URL 质量评估：识别汇总页、错误文档、过期页等，触发重新定位。"""
import re
from urllib.parse import urlparse

from crawler.listing_resolver import is_listing_url, is_notice_page_url

AGGREGATE_TITLE_HINTS = (
    "陆续更新", "夏令营信息", "信息汇总", "一览", "列表", "夏令营管理",
    "招生简章汇总", "xlygs", "夏令营活动报名", "招生网", "夏令营-",
)
SECONDARY_DOC_HINTS = (
    "活动办法", "实施方案", "实施细则", "工作细则", "补充通知",
)
RECAP_TITLE_HINTS = (
    "顺利举办", "圆满落幕", "成功举办", "纪实", "回顾", "活动总结",
    "圆满举行", "完美收官",
)
LOW_VALUE_TITLE_HINTS = (
    "报名指南", "申请指南", "操作指南",
)


def is_pdf_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or ("/upload/files/" in path and ".pdf" in path)


def is_aggregate_notice(title: str, url: str = "") -> bool:
    t = title or ""
    if any(h in t for h in AGGREGATE_TITLE_HINTS):
        return True
    if url and is_listing_url(url):
        return True
    path = urlparse(url).path.lower() if url else ""
    if re.search(r"a20\d{2}xly", path):
        return True
    return False


def is_recap_notice(title: str) -> bool:
    return any(h in (title or "") for h in RECAP_TITLE_HINTS)


def is_secondary_doc(title: str) -> bool:
    """活动办法等附属文档，常不含截止填报时间。"""
    return any(h in (title or "") for h in SECONDARY_DOC_HINTS)


def url_year_hint(url: str) -> int | None:
    if not url:
        return None
    for pat in (
        r"/(\d{4})(\d{2})(\d{2})/",
        r"/(\d{4})/(\d{2})(\d{2})/",
        r"/(\d{4})-(\d{2})-(\d{2})",
        r"/(\d{4})/(\d{2})/",
    ):
        m = re.search(pat, url)
        if m:
            return int(m.group(1))
    return None


def is_stale_url(url: str, min_year: int = 2026) -> bool:
    y = url_year_hint(url)
    return y is not None and y < min_year


def assess_notice_url(item, *, html: str | None = None) -> tuple[str, str]:
    """
    评估当前 URL 是否可信。
    返回 (等级, 原因)：ok / suspicious / bad
    """
    url = item.url or ""
    title = item.title or ""

    if not url:
        return "bad", "无 URL"
    if is_pdf_url(url):
        if item.deadline or item.event_time:
            return "ok", ""
        return "ok", "PDF 通知"
    if is_recap_notice(title):
        return "bad", "活动回顾，非招生通知"
    if is_stale_url(url):
        return "bad", "URL 年份过旧"
    if is_aggregate_notice(title, url):
        return "bad", "汇总/列表页，非单篇通知"
    if is_secondary_doc(title) and not item.deadline:
        if is_notice_page_url(url) and any(k in title for k in ("夏令营", "预推免", "推免")):
            return "ok", ""
        return "suspicious", "附属文档，可能缺少截止时间"
    if any(h in title for h in LOW_VALUE_TITLE_HINTS) and not item.deadline:
        if is_notice_page_url(url) and any(k in title for k in ("夏令营", "预推免", "推免")):
            return "ok", ""
        return "suspicious", "指南类页面，可能非正式通知"
    if html is not None:
        if "截止" not in html and "报名" in html and not item.deadline:
            if len(html) > 500 and "夏令营" in html:
                return "suspicious", "正文未见截止表述"
    return "ok", ""


def should_reresolve_url(item, *, html: str | None = None) -> bool:
    """缺截止填报且 URL 可疑时，应重新定位详情页。"""
    if item.deadline:
        return False
    level, _ = assess_notice_url(item, html=html)
    return level in ("bad", "suspicious")


def score_notice_candidate(
    cand,
    item,
    *,
    html: str | None = None,
) -> float:
    """为 Bing/列表候选通知打分，越高越可能是正确详情页。"""
    score = 0.0
    title = cand.title or ""
    url = cand.url or ""
    snippet = cand.summary or ""

    if is_pdf_url(url):
        if "夏令营" in title or "预推免" in title:
            score += 2.0
        if cand.deadline:
            score += 6.0
        return score
    if is_listing_url(url):
        return -100.0
    if is_recap_notice(title) or is_stale_url(url):
        return -50.0
    if is_aggregate_notice(title, url):
        return -40.0

    if item.university and item.university[:2] in title:
        score += 3.0
    if item.college and item.college[:2] in title:
        score += 3.0
    if "2026" in title or "2027" in title:
        score += 1.0
    if "招生通知" in title or "夏令营活动通知" in title:
        score += 4.0
    if "优秀大学生" in title:
        score += 2.0
    if is_secondary_doc(title):
        score -= 3.0 if not is_notice_page_url(url) else 0.0
    if any(h in title for h in LOW_VALUE_TITLE_HINTS):
        score -= 2.0 if not is_notice_page_url(url) else 0.0

    combined = f"{title} {snippet}"
    if "截止" in combined or "截至" in combined:
        score += 8.0
    if cand.deadline:
        score += 10.0
    if html and "截止" in html:
        score += 6.0
    if url == item.url:
        score -= 1.0
    return score


def score_search_hit(item, target) -> float:
    """检索结果排序：优先学院域、含截止时间的详情页。"""
    ref = type("_Ref", (), {
        "university": target.university,
        "college": target.college,
        "url": "",
    })()
    score = score_notice_candidate(item, ref)
    title = item.title or ""
    url = item.url or ""

    if target.university in title:
        score += 4.0
    if target.college and target.college[:2] in title:
        score += 3.0
    if target.base_url:
        from urllib.parse import urlparse
        host = urlparse(target.base_url).netloc.lower()
        if host and host.replace("www.", "") in url.lower():
            score += 6.0
    if "通知" in title and "夏令营" in title:
        score += 2.0
    if item.summary and ("截止" in item.summary or "截至" in item.summary):
        score += 5.0
    return score
