import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config import settings
from crawler.source_labels import OFFICIAL_LABEL, SEARCH_LABEL, WECHAT_LABEL
from crawler.university_config import (
    CAMP_KEYWORDS,
    ENDED_KEYWORDS,
    EXCELLENT_KEYWORDS,
    FOREIGN_LANG_KEYWORDS,
    LAW_KEYWORDS,
)


@dataclass
class ParsedAnnouncement:
    title: str
    url: str
    publish_date: str | None = None
    deadline: str | None = None
    event_time: str | None = None
    event_format: str | None = None
    event_type: str = "夏令营"
    status: str = "active"
    summary: str | None = None
    source: str = OFFICIAL_LABEL
    university: str | None = None
    college: str | None = None
    college_type: str | None = None
    original_url: str | None = None  # 汇总页/检索命中 URL，下钻后保留
    forum_incomplete: bool = False  # 论坛帖无官方链接且正文不全，需人工补链
    llm_rejected: bool = False  # LLM 分类器判定为噪音


DATE_PATTERNS = [
    re.compile(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})"),
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"(\d{4})/(\d{2})/(\d{2})"),
]

# 日期时间片段（用于报名/填报/举办时间）
_DT_CORE = (
    r"(\d{4}[年\-/.]\d{1,2}[月\-/.]\d{1,2}[日号]?"
    r"(?:\s*(?:\d{1,2}:\d{2}(?::\d{2})?|24:00(?::00)?))?)"
)
_DT_SHORT_INNER = (
    r"(?:\d{4}[年\-/.])?\d{1,2}[月\-/.]\d{1,2}[日号]?"
    r"(?:\s*(?:\d{1,2}:\d{2}(?::\d{2})?|24:00(?::00)?)?)?"
)
_DT_SHORT = rf"({_DT_SHORT_INNER})"
_RANGE = r"(?:至|到|—|~|-)"
_MONTH_TEN = r"(?:上旬|中旬|下旬|中下旬)"

REGISTRATION_OPEN_PATTERNS = [
    re.compile(rf"(?:网上)?(?:报名|填报|申请)(?:开始|开放)(?:时间|日期)?[为：:\s]*{_DT_CORE}"),
    re.compile(rf"(?:系统|报名系统)(?:开放|开启|开通)(?:时间|日期)?[为：:\s]*{_DT_CORE}"),
    re.compile(rf"(?:开放|开始)(?:网上)?(?:报名|填报)(?:时间|日期)?[为：:\s]*{_DT_CORE}"),
    re.compile(rf"自{_DT_CORE}(?:起|开始)(?:接受|开放)?(?:网上)?(?:报名|填报)?"),
    re.compile(rf"(?:于|从){_DT_CORE}(?:起|开始)(?:接受|开放)?(?:网上)?(?:报名|填报)?"),
    re.compile(rf"开放时间[为：:\s]*{_DT_CORE}"),
    re.compile(rf"({_DT_SHORT_INNER})\s*开放(?:[，,]\s*{_DT_SHORT_INNER}\s*关闭)?"),
    re.compile(rf"申请时间[为：:\s]*{_DT_CORE}"),
    re.compile(rf"报名时间[为：:\s]*{_DT_CORE}"),
    re.compile(rf"填报时间[为：:\s]*{_DT_CORE}"),
    re.compile(rf"接收(?:推免生|申请)(?:时间|报名)?[为：:\s]*{_DT_CORE}"),
    re.compile(
        rf"({_DT_CORE})\s*{_RANGE}\s*({_DT_SHORT_INNER})"
        r"(?:期间|内)?(?:进行)?(?:网上)?(?:报名|申请|填报|接收)"
    ),
]

REGISTRATION_DEADLINE_PATTERNS = [
    re.compile(rf"(?:网上)?(?:报名|填报|申请)(?:截止(?:时间|日期)?|截至|结束)[为：:\s]*{_DT_CORE}"),
    re.compile(rf"(?:系统|报名系统)(?:关闭|截止)(?:时间|日期)?[为：:\s]*{_DT_CORE}"),
    re.compile(rf"({_DT_SHORT_INNER})\s*关闭"),
    re.compile(rf"(?:停止|暂停)(?:网上)?(?:报名|填报)(?:时间)?[为：:\s]*{_DT_CORE}"),
    re.compile(rf"(?:请于|在){_DT_CORE}(?:前|之前)(?:完成)?(?:网上)?(?:报名|填报|提交)"),
    re.compile(
        rf"(?:请于|在)\s*({_DT_SHORT_INNER})\s*(?:前|之前)"
        r"(?:登录|进行|完成|提交|申请|填报|网上申请)?"
    ),
    re.compile(rf"截止(?:时间|日期)[为：:\s]*{_DT_CORE}(?:前|止)?"),
    re.compile(rf"截止时间[为：:\s]*{_DT_CORE}"),
    re.compile(rf"报名(?:时间|日期)?[为：:\s]*{_DT_CORE}\s*{_RANGE}\s*({_DT_SHORT_INNER})"),
    re.compile(
        rf"于\s*{_DT_CORE}\s*(?:前|之前)\s*(?:发送|提交|邮寄|寄至|送达|完成|将|通过)"
    ),
    re.compile(
        rf"({_DT_CORE})\s*(?:前|之前)\s*(?:发送|提交|邮寄|寄至|送达|完成|将材料|将申请)"
    ),
    re.compile(rf"(?:材料|申请(?:材料)?|扫描件).*?于\s*{_DT_CORE}\s*(?:前|之前)"),
    re.compile(
        rf"({_DT_CORE})\s*{_RANGE}\s*({_DT_SHORT_INNER})"
        r"(?:截止|结束|为止|前截止)"
    ),
]

REGISTRATION_RANGE_PATTERN = re.compile(
    rf"(?:网上)?(?:报名|填报|申请)(?:时间|日期)?[为：:\s]*{_DT_CORE}\s*{_RANGE}\s*{_DT_SHORT}"
)

OPEN_CLOSE_PAIR_PATTERN = re.compile(
    rf"({_DT_SHORT_INNER})\s*开放[，,]\s*({_DT_SHORT_INNER})\s*关闭"
)

FROM_NOW_UNTIL_PATTERN = re.compile(
    rf"(?:报名时间[为：:\s]*)?即日起\s*(?:至\s*)?({_DT_SHORT_INNER})"
)

REGISTRATION_TIME_RANGE_PATTERN = re.compile(
    rf"(?:报名|申请)时间[为：:\s]*({_DT_SHORT_INNER})\s*{_RANGE}\s*({_DT_SHORT_INNER})"
)

REGISTRATION_DEADLINE_EXPLICIT = re.compile(
    rf"报名截止时间[为：:\s]*({_DT_SHORT_INNER})(?:\s*24[:：]00)?"
)

# 发布/更新时间（页眉常见格式）
NOTICE_HEADER_PATTERN = re.compile(
    r"发布时间[：:\s]*(\d{4}[/\-年]\d{1,2}[/\-月]\d{1,2}[日号]?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
)

NOTICE_PUBLISH_PATTERNS = [
    re.compile(rf"发布(?:时间|日期)[为：:\s]*{_DT_CORE}"),
    re.compile(rf"发文(?:时间|日期)[为：:\s]*{_DT_CORE}"),
    re.compile(rf"更新时间[为：:\s]*{_DT_CORE}"),
    re.compile(r"时间[：:\s]*(\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"),
]

EVENT_TIME_PATTERNS = [
    re.compile(
        rf"开营时间[为：:\s]*"
        rf"(\d{{4}}[年\-/.]\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?\s*{_RANGE}\s*"
        rf"(?:\d{{4}}[年\-/.])?\d{{1,2}}[月\-/.]?\d{{1,2}}[日号]?(?:\s*\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?)"
    ),
    re.compile(
        rf"(\d{{4}}[年\-/.]\d{{1,2}}月\d{{1,2}}日\s*{_RANGE}\s*\d{{1,2}}日)"
        r"(?=.{0,50}(?:夏令营|活动|开营|报到|离营|行程|营员)?)"
    ),
    re.compile(
        rf"活动时间[\s：:]*"
        rf"(\d{{4}}[年\-/.]\d{{1,2}}月\d{{1,2}}日\s*{_RANGE}\s*\d{{1,2}}日?)"
    ),
    re.compile(
        rf"拟定于\s*(\d{{4}}[年\-/.]\d{{1,2}}月\d{{1,2}}日\s*{_RANGE}\s*\d{{1,2}}日)"
    ),
    re.compile(
        rf"将于\s*(\d{{1,2}}[月\-/.]\d{{1,2}}日\s*{_RANGE}\s*\d{{1,2}}(?:月)?\d{{1,2}}日)\s*举办"
    ),
    re.compile(
        rf"(?:夏令营|入营|研学)(?:活动)?(?:举办)?(?:时间为|定于|于|安排在)[：:\s]*"
        rf"(\d{{4}}[年\-/.]\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?\s*{_RANGE}\s*"
        rf"(?:\d{{4}}[年\-/.])?\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?(?:\s*\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?)"
    ),
    re.compile(
        rf"(?:举办|活动|开营|夏令营)(?:时间为|时间[为：:\s]*|定于|于)"
        rf"(\d{{4}}[年\-/.]\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?\s*{_RANGE}\s*"
        rf"(?:\d{{4}}[年\-/.])?\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?(?:\s*\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?)"
    ),
    re.compile(
        rf"开营时间[为：:\s]*"
        rf"(\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?\s*{_RANGE}\s*\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?)"
    ),
    re.compile(rf"营期[为：:\s]*(\d{{4}}[年\-/.]\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?\s*{_RANGE}\s*(?:\d{{4}}[年\-/.])?\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?)"),
    re.compile(
        rf"(\d{{4}}[年\-/.]\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?\s*{_RANGE}\s*"
        rf"(?:\d{{4}}[年\-/.])?\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?(?:\s*\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?)"
        r"(?=.{0,30}(?:夏令营|开营|入营|活动))"
    ),
    re.compile(
        rf"(\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?\s*{_RANGE}\s*\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?)"
        r"(?=.{0,30}(?:夏令营|开营|入营|活动))"
    ),
    re.compile(
        rf"(\d{{4}}[年\-/.]\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?)"
        r"(?=.{0,24}(?:举办|开营|进行|开营式|活动安排|夏令营活动))"
    ),
    re.compile(r"(?:夏令营|活动|开营)(?:举办)?(?:时间为|定于|于)[：:\s]*([^。；\n]{4,40})"),
    re.compile(
        rf"(?:夏令营安排|活动安排|日程安排).*?时间[：:\s]*(\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?)"
    ),
    re.compile(
        rf"(\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?)[：:\s]*(?:夏令营)?(?:线上)?开营"
    ),
    re.compile(
        rf"活动时间[为：:\s]*(\d{{4}}[年\-/.]\d{{1,2}}月{_MONTH_TEN}[^。；\n]*)"
    ),
    re.compile(
        rf"(?:一、)?活动时间[为：:\s]*(\d{{4}}[年\-/.]\d{{1,2}}月{_MONTH_TEN})"
    ),
    re.compile(
        rf"拟于\s*(\d{{4}}[年\-/.]\d{{1,2}}月{_MONTH_TEN})"
        r"(?:举办|举行|开展)?"
    ),
    re.compile(
        rf"(\d{{4}}[年\-/.]\d{{1,2}}月{_MONTH_TEN})"
        r"(?=.{0,30}(?:举办|举行|夏令营|开营))"
    ),
]

EVENT_FORMAT_HYBRID = re.compile(
    r"线上.{0,12}线下|线下.{0,12}线上|线上线下|线上与线下|线下与线上|"
    r"同步(?:进行|开展)|混合(?:模式|形式)?(?:举办)?"
)
EVENT_FORMAT_ONLINE = re.compile(
    r"线上(?:举办|进行|开展|开营|夏令营|活动|形式|方式|参与|参加)?|"
    r"在线(?:举办|进行|开展)|网络(?:远程)?(?:举办|进行|开营)|"
    r"远程(?:举办|开营|进行)|腾讯会议|zoom|钉钉|以线上|"
    r"会议号|会议链接|腾讯会议号|线上平台"
)
EVENT_FORMAT_OFFLINE = re.compile(
    r"线下(?:举办|进行|开展|开营|夏令营|活动|形式|方式|参与|参加)?|"
    r"现场(?:举办|参加|开营|进行)|到校(?:参加|举办)|来校(?:参加|举办)|"
    r"实地(?:举办|参加)|线下开营|举办形式为现场|形式为现场|"
    r"入营报到|报到地点|举办地点|活动地址|校区|教学楼|酒店|"
    r"参观走访|交通费报销|提供.*食宿|食宿(?:安排|提供)|"
    r"校内餐饮|住宿费.*自理|往返交通"
)

CONTENT_SELECTORS = (
    ".v_news_content", ".article", "#content", ".content", "article",
    ".wp_articlecontent", ".news-content", ".detail", ".TRS_Editor",
    ".main-text", ".text", "#vsb_content",
)

YEAR_PATTERN = re.compile(r"(20\d{2})")


def extract_all_years(text: str) -> list[int]:
    if not text:
        return []
    return [int(y) for y in YEAR_PATTERN.findall(text)]


def is_year_eligible(
    title: str,
    publish_date: str | None = None,
    deadline: str | None = None,
) -> bool:
    """仅保留 min_notice_year（默认2026）及之后的通知。"""
    min_year = settings.min_notice_year
    max_year = min_year + 2  # 截止日最多落到后年，过滤 2069 等解析脏数据

    title_years = extract_all_years(title)
    if title_years and max(title_years) < min_year:
        return False

    years = list(title_years)
    for date_str in (publish_date, deadline):
        if date_str and len(date_str) >= 4:
            try:
                y = int(date_str[:4])
                if min_year - 1 <= y <= max_year:
                    years.append(y)
            except ValueError:
                pass

    if not years:
        if any(k in (title or "") for k in ("夏令营", "预推免", "推免", "暑期", "优营")):
            return datetime.now().year >= min_year
        return False
    return max(years) >= min_year


def is_relevant(title: str, college_type: str, *, from_wechat: bool = False) -> bool:
    from crawler.noise_filter import passes_title_filter

    if not passes_title_filter(title or ""):
        return False

    has_camp = any(kw in title for kw in CAMP_KEYWORDS)
    has_excellent = any(kw in title for kw in EXCELLENT_KEYWORDS)
    if not has_camp and not has_excellent:
        return False

    if from_wechat:
        return True

    if college_type == "law":
        return any(kw in title for kw in LAW_KEYWORDS)
    return any(kw in title for kw in FOREIGN_LANG_KEYWORDS)


def detect_status(title: str, deadline: str | None = None) -> str:
    if any(kw in title for kw in EXCELLENT_KEYWORDS):
        return "excellent_list"
    if any(kw in title for kw in ENDED_KEYWORDS):
        return "ended"

    if deadline:
        dt = parse_datetime_value(deadline)
        if dt and dt < datetime.now():
            return "ended"

    return "active"


def effective_status(status: str, deadline: str | None = None) -> str:
    """按截止填报时间判定展示状态（优营名单不受截止影响）。"""
    if status == "excellent_list":
        return "excellent_list"
    if deadline:
        dt = parse_datetime_value(deadline)
        if dt and dt < datetime.now():
            return "ended"
    return status if status in ("active", "ended", "excellent_list") else "active"


def is_past_deadline(deadline: str | None) -> bool:
    if not deadline:
        return False
    dt = parse_datetime_value(deadline)
    return bool(dt and dt < datetime.now())


def detect_event_type(title: str) -> str:
    if "预推免" in title or "推免" in title:
        return "预推免"
    if "暑期学校" in title:
        return "暑期学校"
    if "开放日" in title:
        return "开放日"
    if any(kw in title for kw in EXCELLENT_KEYWORDS):
        return "优营名单"
    return "夏令营"


def extract_date(text: str) -> str | None:
    for pattern in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


def _ref_year(text: str) -> int:
    if not text:
        return datetime.now().year
    years: list[int] = []
    for m in re.finditer(r"(20\d{2})", text):
        tail = text[m.end(): m.end() + 2].strip()
        if tail.startswith("届"):
            continue
        years.append(int(m.group(1)))
    return max(years) if years else datetime.now().year


def compact_spaced_text(text: str) -> str:
    """合并网页中被打散的空格（如「6 月 2 6 日」「开营 时 间」）。"""
    if not text:
        return ""
    for _ in range(5):
        text = re.sub(r"(\d)\s+(?=\d)", r"\1", text)
        text = re.sub(r"(\d)\s+(?=[年月日:：\-/])", r"\1", text)
        text = re.sub(r"([年月日时分秒])\s+", r"\1", text)
    for kw in ("开营", "报名", "截止", "申请", "举办", "活动", "填报", "发布", "拟于", "将于"):
        text = re.sub(rf"({kw})\s+", r"\1", text)
    text = re.sub(r"拟\s+于", "拟于", text)
    text = re.sub(r"将\s+于", "将于", text)
    text = re.sub(r"至\s+(\d)", r"至\1", text)
    return text


def focus_notice_text(text: str) -> str:
    """聚焦含报名/开营信息的正文，过滤学院简介等噪声。"""
    text = compact_spaced_text(text)
    for anchor in (
        "申请时间", "报名时间", "网上报名", "报名系统", "填报时间",
        "活动时间", "开营时间", "夏令营安排", "夏令营", "入营时间", "招生简章",
    ):
        idx = text.find(anchor)
        if idx >= 0:
            return text[max(0, idx - 80): idx + 6000]
    chunks = re.split(r"(?=[一二三四五六七八九十百]+[、．.])", text)
    picked = [
        c for c in chunks
        if any(k in c for k in ("夏令营", "报名", "开营", "填报", "申请", "截止", "入营", "暑期", "优营"))
    ]
    if picked:
        return "\n".join(picked)
    return text


def extract_date_from_url(url: str) -> str | None:
    """从通知 URL 推断发布日期。"""
    if not url:
        return None
    for pat in (
        r"/(\d{4})(\d{2})(\d{2})/",
        r"/(\d{4})/(\d{2})(\d{2})/",
        r"/(\d{4})-(\d{2})-(\d{2})",
    ):
        m = re.search(pat, url)
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d} 00:00:00"
    return None


def parse_datetime_value(val: str) -> datetime | None:
    """解析 YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD。"""
    if not val:
        return None
    val = val.strip()
    for fmt, length in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
    ):
        try:
            return datetime.strptime(val[:length], fmt)
        except ValueError:
            continue
    return None


def normalize_datetime(raw: str, *, is_deadline: bool = False, ref_text: str = "") -> str | None:
    """将中文日期时间规范为 YYYY-MM-DD HH:MM:SS。"""
    if not raw:
        return None
    raw = raw.strip()
    ref_year = _ref_year(ref_text or raw)

    ym = re.search(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})", raw)
    if ym:
        y, mo, d = int(ym.group(1)), int(ym.group(2)), int(ym.group(3))
    else:
        md = re.search(r"(\d{1,2})[月\-/.](\d{1,2})[日号]?", raw)
        if not md:
            return None
        y, mo, d = ref_year, int(md.group(1)), int(md.group(2))

    tm = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    noon = re.search(r"(\d{1,2})?\s*点", raw)
    if re.search(r"中午|午间", raw):
        h, mi, sec = 12, 0, 0
    elif re.search(r"下午", raw) and noon and not tm:
        h = int(noon.group(1) or 12)
        if h < 12:
            h += 12
        mi, sec = 0, 0
    elif re.search(r"上午", raw) and noon and not tm:
        h = int(noon.group(1) or 9)
        mi, sec = 0, 0
    elif noon and not tm:
        h, mi, sec = int(noon.group(1) or 12), 0, 0
    elif tm:
        h, mi, sec = int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0)
        if h == 24 and mi == 0 and sec == 0:
            h, mi, sec = 23, 59, 59
    elif is_deadline:
        h, mi, sec = 23, 59, 59
    else:
        h, mi, sec = 0, 0, 0

    return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{sec:02d}"


def _first_match_datetime(patterns: list[re.Pattern], text: str, *, is_deadline: bool = False) -> str | None:
    for pattern in patterns:
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(1).strip()
        normalized = normalize_datetime(raw, is_deadline=is_deadline, ref_text=text)
        if normalized:
            return normalized
    return None


def extract_registration_period(text: str) -> tuple[str | None, str | None]:
    """提取「X至Y期间报名/登录系统」类时间段。"""
    period_re = re.compile(rf"({_DT_SHORT_INNER})\s*{_RANGE}\s*({_DT_SHORT_INNER})")
    best: tuple[int, str | None, str | None] | None = None
    for m in period_re.finditer(text):
        before = text[max(0, m.start() - 35): m.start()]
        if any(k in before for k in ("举办", "拟定于", "将于", "开营式", "日程", "食宿")):
            continue
        window = text[max(0, m.start() - 40): m.end() + 120]
        if "线下举办" in window or "举办将" in before:
            continue
        if any(k in window for k in ("开营", "夏令营", "入营", "活动")) and not any(
            k in window for k in ("报名", "登录", "系统", "yzbm", "填报", "申请")
        ):
            continue
        if not any(k in window for k in ("报名", "登录", "填报", "申请", "系统", "yzbm", "研究生招生")):
            continue
        start = normalize_datetime(m.group(1).strip(), is_deadline=False, ref_text=text)
        end_raw = m.group(2).strip()
        tail = text[m.end(2): m.end(2) + 20]
        end_raw = _append_time_qualifier(end_raw, tail)
        end = normalize_datetime(end_raw, is_deadline=True, ref_text=text)
        if not start and not end:
            continue
        score = sum(5 for k in ("登录", "报名系统", "yzbm", "网上报名", "填报") if k in window)
        if best is None or score > best[0]:
            best = (score, start, end)
    if best:
        return best[1], best[2]
    return None, None


def _append_time_qualifier(raw: str, tail: str) -> str:
    """把「上午12点」「中午12点」「24:00」等紧挨在日期后的时间补进 raw。"""
    if re.search(r"中午|午间", tail) and not re.search(r"中午|午间", raw):
        noon = re.search(r"(\d{1,2})点", tail)
        h = noon.group(1) if noon else "12"
        return f"{raw} 中午{h}点"
    am = re.search(r"(上午|下午)\s*(\d{1,2})点", tail)
    if am and am.group(1) not in raw:
        return f"{raw} {am.group(1)}{am.group(2)}点"
    if re.search(r"24[:：]00", tail) and "24" not in raw:
        return f"{raw} 24:00"
    return raw


def extract_registration_open(text: str) -> str | None:
    """网上系统开放填报的开始时间。"""
    m = OPEN_CLOSE_PAIR_PATTERN.search(text)
    if m:
        start = normalize_datetime(m.group(1).strip(), is_deadline=False, ref_text=text)
        if start:
            return start
    if FROM_NOW_UNTIL_PATTERN.search(text):
        notice = extract_notice_publish_date(text)
        if notice:
            return notice
    m = REGISTRATION_TIME_RANGE_PATTERN.search(text)
    if m:
        start = normalize_datetime(m.group(1).strip(), is_deadline=False, ref_text=text)
        if start:
            return start
    open_time = _first_match_datetime(REGISTRATION_OPEN_PATTERNS, text, is_deadline=False)
    if open_time:
        return open_time
    start, _ = extract_registration_period(text)
    return start


def extract_registration_deadline(text: str) -> str | None:
    """网上系统停止填报的截止时间（精确到秒）。"""
    m = OPEN_CLOSE_PAIR_PATTERN.search(text)
    if m:
        end = normalize_datetime(m.group(2).strip(), is_deadline=True, ref_text=text)
        if end:
            return end
    m = REGISTRATION_DEADLINE_EXPLICIT.search(text)
    if m:
        raw = m.group(1).strip()
        tail = text[m.end(): m.end() + 20]
        raw = _append_time_qualifier(raw, tail)
        if re.search(r"24[:：]00", m.group(0)):
            raw = f"{raw} 24:00"
        end = normalize_datetime(raw, is_deadline=True, ref_text=text)
        if end:
            return end
    m = REGISTRATION_TIME_RANGE_PATTERN.search(text)
    if m:
        end_raw = m.group(2).strip()
        tail = text[m.end(2): m.end(2) + 20]
        end_raw = _append_time_qualifier(end_raw, tail)
        end = normalize_datetime(end_raw, is_deadline=True, ref_text=text)
        if end:
            return end
    m = FROM_NOW_UNTIL_PATTERN.search(text)
    if m:
        raw = m.group(1).strip()
        tail = text[m.end(): m.end() + 30]
        raw = _append_time_qualifier(raw, tail)
        end = normalize_datetime(raw, is_deadline=True, ref_text=text)
        if end:
            return end
    dl = _first_match_datetime(REGISTRATION_DEADLINE_PATTERNS, text, is_deadline=True)
    if dl:
        return dl
    m = REGISTRATION_RANGE_PATTERN.search(text)
    if m:
        end_raw = m.group(2).strip()
        start_raw = m.group(1).strip()
        ref_text = text if extract_all_years(start_raw) else f"{_ref_year(text)}年{end_raw}"
        return normalize_datetime(end_raw, is_deadline=True, ref_text=ref_text)
    _, end = extract_registration_period(text)
    return end


def extract_notice_publish_date(text: str) -> str | None:
    """通知在官网发布的日期（无开放填报时间时的兜底）。"""
    dt = _first_match_datetime(NOTICE_PUBLISH_PATTERNS, text, is_deadline=False)
    if dt:
        return dt
    m = NOTICE_HEADER_PATTERN.search(text)
    if m:
        raw = m.group(1).replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
        return normalize_datetime(raw, is_deadline=False, ref_text=text)
    tail = text.strip()[-80:]
    m = re.search(rf"(\d{{4}}[年\-/.]\d{{1,2}}[月\-/.]\d{{1,2}}[日号]?)\s*$", tail)
    if m:
        return normalize_datetime(m.group(1), is_deadline=False, ref_text=text)
    return None


def extract_notice_date_from_html(html: str) -> str | None:
    """从详情页 HTML 提取通知发布日期。"""
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    for sel in (".time", ".date", ".pubtime", ".publish", ".article-time", ".news-time"):
        el = soup.select_one(sel)
        if el:
            dt = extract_notice_publish_date(el.get_text(" ", strip=True))
            if dt:
                return dt
    for meta_name in ("publishdate", "PubDate", "pubdate", "date"):
        meta = soup.find("meta", attrs={"name": meta_name})
        if meta and meta.get("content"):
            dt = extract_notice_publish_date(meta["content"])
            if dt:
                return dt
    header = soup.get_text(" ", strip=True)[:2000]
    return extract_notice_publish_date(header)


def resolve_publish_date(text: str, html: str | None = None, url: str | None = None) -> str | None:
    """发布时间 = 系统开放填报时间；若无则用通知发布日期。"""
    open_time = extract_registration_open(text)
    if open_time:
        return open_time
    m = REGISTRATION_RANGE_PATTERN.search(text)
    if m:
        start = normalize_datetime(m.group(1).strip(), is_deadline=False, ref_text=text)
        if start:
            return start
    if html:
        notice = extract_notice_date_from_html(html)
        if notice:
            return notice
    notice = extract_notice_publish_date(text)
    if notice:
        return notice
    if url:
        return extract_date_from_url(url)
    return None


def extract_deadline(text: str) -> str | None:
    """截止时间 = 网上系统填报截止（精确到秒）。"""
    return extract_registration_deadline(text)


def _clean_event_time(raw: str) -> str:
    raw = re.sub(r"\s+", " ", raw.strip())
    raw = re.sub(r"^[：:，,\s]+", "", raw)
    raw = re.sub(r"[（(][^）)]*(?:另行通知|待定|以.*为准)[^）)]*[）)]", "", raw).strip()
    for stop in ("，", "。", "；", ";", "报名", "请于", "材料", "截止", "采用", "将", "举行", "填报"):
        idx = raw.find(stop)
        if idx > 0:
            raw = raw[:idx]
    bad = ("发布时间", "点击", "分享", "下载", "附件", "作者", "来源", "浏览", "字号", "上一篇", "下一篇")
    if any(b in raw for b in bad):
        return ""
    if "报名" in raw and "夏令营" not in raw and "开营" not in raw:
        return ""
    if len(raw) > 50 and not re.search(r"\d{1,2}[月\-/.]|至|到|\d{4}", raw):
        return ""
    if not re.search(r"\d{1,2}[月\-/.]|至|到|\d{4}-\d{2}-\d{2}|\d{4}年|上旬|中旬|下旬", raw):
        return ""
    return raw[:80] if len(raw) > 80 else raw


def extract_event_time(text: str) -> str | None:
    """举办时间 = 夏令营/开营实际进行的时间（非报名时间）。"""
    if not text:
        return None
    for sent in re.split(r"[。；\n]", text):
        if not sent.strip():
            continue
        if any(k in sent for k in ("登录", "报名系统", "yzbm", "填报", "申请系统")) and not any(
            k in sent for k in ("开营", "进校", "入营", "活动安排", "夏令营活动")
        ):
            continue
        for pattern in EVENT_TIME_PATTERNS:
            m = pattern.search(sent)
            if m:
                raw = _clean_event_time(m.group(1))
                if len(raw) >= 4:
                    return raw
    for pattern in EVENT_TIME_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = _clean_event_time(m.group(1))
            if len(raw) >= 4 and "报名" not in raw[:20]:
                return raw
    return None


def extract_all_times(text: str, html: str | None = None, url: str | None = None) -> dict[str, str | None]:
    """统一提取：开放填报 / 截止填报 / 举办时间。"""
    focused = focus_notice_text(text)
    start_p, end_p = extract_registration_period(focused)
    if not start_p and not end_p:
        start_p, end_p = extract_registration_period(text)

    publish = resolve_publish_date(focused, html, url)
    if not publish:
        publish = resolve_publish_date(text, html, url)
    if start_p and not publish:
        publish = start_p

    deadline = extract_registration_deadline(focused) or extract_registration_deadline(text)
    if end_p and not deadline:
        deadline = end_p

    event_time = extract_event_time(focused) or extract_event_time(text)

    return {
        "publish_date": publish,
        "deadline": deadline,
        "event_time": event_time,
    }


def core_times_complete(item: ParsedAnnouncement) -> bool:
    """开放填报、截止填报、举办时间三项是否齐全。"""
    return bool(item.publish_date and item.deadline and item.event_time)


def _prefer_datetime(new: str | None, old: str | None) -> str | None:
    if not new:
        return old
    if not old:
        return new
    if " 00:00:00" in old and " 00:00:00" not in new:
        return new
    if len(new) > len(old):
        return new
    return old


def merge_times_into_item(
    item: ParsedAnnouncement,
    text: str,
    html: str | None = None,
) -> ParsedAnnouncement:
    """从正文合并时间字段（不覆盖更精确的已有值）。"""
    for chunk in (focus_notice_text(text), text):
        times = extract_all_times(chunk, html, item.url)
        item.publish_date = _prefer_datetime(times["publish_date"], item.publish_date)
        item.deadline = _prefer_datetime(times["deadline"], item.deadline)
        if times["event_time"] and not item.event_time:
            item.event_time = times["event_time"]

    fmt = extract_event_format(text) or extract_event_format(focus_notice_text(text))
    if fmt and not item.event_format:
        item.event_format = fmt

    if item.deadline:
        item.status = detect_status(item.title, item.deadline)
    return item


def apply_extracted_times(
    item: ParsedAnnouncement,
    text: str,
    html: str | None = None,
) -> ParsedAnnouncement:
    return merge_times_into_item(item, text, html)


def extract_event_format(text: str) -> str | None:
    """识别举办形式：线上 / 线下 / 线上线下。"""
    if not text:
        return None
    compact = re.sub(r"\s+", "", text)
    if EVENT_FORMAT_HYBRID.search(compact):
        return "线上线下"
    online = bool(EVENT_FORMAT_ONLINE.search(compact))
    offline = bool(EVENT_FORMAT_OFFLINE.search(compact))
    if online and offline:
        return "线上线下"
    if online:
        return "线上"
    if offline:
        return "线下"
    if "线上" in compact and "线下" in compact:
        return "线上线下"
    if "线上" in compact and any(k in compact for k in ("夏令营", "开营", "举办", "活动")):
        return "线上"
    if "线下" in compact and any(k in compact for k in ("夏令营", "开营", "举办", "活动")):
        return "线下"
    return None


def extract_table_text(html: str) -> str:
    """从 HTML 表格提取结构化文本（合并单元格按行拼接）。"""
    if not html or "<table" not in html.lower():
        return ""
    soup = BeautifulSoup(html, "lxml")
    parts: list[str] = []
    for table in soup.find_all("table"):
        rows: list[str] = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            parts.append("\n".join(rows))
    return "\n\n".join(parts)


def extract_page_text(html: str, *, title: str = "") -> str:
    """从通知详情页 HTML 或 PDF 纯文本提取正文；含表格型通知时优先合并表格文本。"""
    if not html:
        return ""
    from crawler.pdf_extractor import looks_like_html

    plain = not looks_like_html(html)
    if len(html) < (80 if plain else 200):
        return ""
    if plain:
        text = compact_spaced_text(_trim_boilerplate(html[:12000]))
        return text if len(text) >= 50 else ""
    soup = BeautifulSoup(html, "lxml")
    table_text = ""
    if title and "夏令营" in title:
        table_text = extract_table_text(html)
    for tag in soup(["script", "style", "nav", "footer", "header", "iframe"]):
        tag.decompose()
    for sel in CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if len(text) >= 80:
                body = _trim_boilerplate(text[:12000])
                if table_text and table_text not in body:
                    return f"{body}\n\n[表格]\n{table_text[:6000]}"
                return body
    title_el = soup.find(["h1", "h2"])
    if title_el:
        block = title_el.find_parent(["div", "article", "td"]) or title_el.parent
        if block:
            text = block.get_text(" ", strip=True)
            if len(text) >= 80:
                body = _trim_boilerplate(text[:12000])
                if table_text and table_text not in body:
                    return f"{body}\n\n[表格]\n{table_text[:6000]}"
                return body
    body = soup.find("body")
    if body:
        body_text = _trim_boilerplate(body.get_text(" ", strip=True)[:12000])
        if body_text:
            best_text = _best_content_block(soup)
            if best_text and len(best_text) >= 120:
                body = _trim_boilerplate(best_text[:12000])
            if table_text and table_text not in body:
                return f"{body}\n\n[表格]\n{table_text[:6000]}"
            return body_text
    fallback = _trim_boilerplate(soup.get_text(" ", strip=True)[:12000])
    if table_text and table_text not in fallback:
        return f"{fallback}\n\n[表格]\n{table_text[:6000]}"
    return fallback


def _best_content_block(soup: BeautifulSoup) -> str:
    """在导航较多的页面中，选取含夏令营/报名关键词最集中的块。"""
    keywords = ("夏令营", "报名", "截止", "申请", "填报", "开营", "入营", "暑期")
    best = ""
    best_score = 0
    for el in soup.find_all(["div", "td", "article", "section"]):
        t = el.get_text(" ", strip=True)
        if len(t) < 120 or len(t) > 18000:
            continue
        nav_hits = sum(t.count(k) for k in ("首页", "English", "下载专区", "友情链接"))
        score = sum(3 for k in keywords if k in t) - nav_hits
        if score > best_score:
            best_score = score
            best = t
    return best if best_score >= 4 else ""


def _trim_boilerplate(text: str) -> str:
    """去掉页眉页脚等噪声段落。"""
    noise = ("首页", "返回顶部", "版权所有", "ICP", "友情链接", "扫一扫", "微信公众号")
    parts = re.split(r"[。\n]", text)
    kept = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 6]
    kept = [p for p in kept if not any(n in p for n in noise)]
    return "。".join(kept) if kept else text


def enrich_from_html(item: ParsedAnnouncement, html: str) -> ParsedAnnouncement:
    """从详情页 HTML 补全开放填报、截止填报、举办时间与举办形式。"""
    text = extract_page_text(html, title=item.title or "")
    if not text:
        if item.url:
            url_pub = extract_date_from_url(item.url)
            if url_pub:
                item.publish_date = _prefer_datetime(url_pub, item.publish_date)
        return item
    if not item.summary or len(item.summary or "") < 80:
        item.summary = text[:500]

    merge_times_into_item(item, text, html)
    return item


def enrich_dates(title: str, summary: str | None = None) -> tuple[str | None, str | None, str | None, str | None]:
    """从标题与摘要中提取开放填报/截止填报/举办时间/举办形式（搜索摘要场景）。"""
    combined = f"{title} {summary or ''}"
    times = extract_all_times(combined)
    return (
        times["publish_date"],
        times["deadline"],
        times["event_time"],
        extract_event_format(combined),
    )


def normalize_url(href: str, base_url: str) -> str:
    if not href or href.startswith("javascript"):
        return ""
    url = urljoin(base_url, href)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    if "baidu.com" in parsed.netloc or "bing.com" in parsed.netloc:
        return ""
    if not parsed.netloc:
        return ""
    return url.split("#")[0]


def is_valid_announcement_url(url: str) -> bool:
    if not url.startswith("http"):
        return False
    parsed = urlparse(url)
    blocked = ("baidu.com", "bing.com", "sogou.com")
    if any(d in parsed.netloc for d in blocked):
        return False
    if "mp.weixin.qq.com" in parsed.netloc:
        return "/s" in parsed.path or "mp.weixin.qq.com/s?" in url
    if "eeban.com" in parsed.netloc and re.search(r"/thread-\d+", parsed.path or ""):
        return True
    return "edu.cn" in parsed.netloc or "ac.cn" in parsed.netloc or "edu.com" in parsed.netloc


def is_html_notice_url(url: str) -> bool:
    """是否为可抓取的 HTML 通知页（含 .htm / .html）。"""
    from crawler.url_quality import is_pdf_url
    from crawler.listing_resolver import is_notice_page_url

    if not url.startswith("http"):
        return False
    if is_pdf_url(url):
        return False
    return is_notice_page_url(url) or url.lower().split("?")[0].endswith(
        (".htm", ".html", ".shtml", ".asp", ".aspx", ".jsp", ".php")
    )


def parse_news_list(
    html: str,
    base_url: str,
    college_type: str,
    *,
    board: str | None = None,
    phase: str | None = None,
) -> list[ParsedAnnouncement]:
    from crawler.boards import is_relevant_for_crawl

    soup = BeautifulSoup(html, "lxml")
    results: list[ParsedAnnouncement] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)
        if len(title) < 8 or len(title) > 200:
            continue

        if board and phase:
            parent = a.find_parent(["li", "tr", "div"])
            context = parent.get_text(" ", strip=True) if parent else title
            if not is_relevant_for_crawl(title, board, phase, summary=context):
                continue
        elif not is_relevant(title, college_type):
            continue

        url = normalize_url(a["href"], base_url)
        if not url or url in seen or not is_valid_announcement_url(url):
            continue
        seen.add(url)

        parent = a.find_parent(["li", "tr", "div"])
        context = parent.get_text(" ", strip=True) if parent else title
        tmp = ParsedAnnouncement(title=title, url=url)
        apply_extracted_times(tmp, context)
        publish_date = tmp.publish_date
        deadline = tmp.deadline
        event_time = tmp.event_time
        event_format = extract_event_format(context)
        event_type = detect_event_type(title)
        status = detect_status(title, deadline)

        if not is_year_eligible(title, publish_date, deadline):
            continue

        results.append(ParsedAnnouncement(
            title=title,
            url=url,
            publish_date=publish_date,
            deadline=deadline,
            event_time=event_time,
            event_format=event_format,
            event_type=event_type,
            status=status,
            source=OFFICIAL_LABEL,
        ))

    return results


def parse_search_results(
    html: str,
    college_type: str,
    *,
    board: str | None = None,
    phase: str | None = None,
) -> list[ParsedAnnouncement]:
    """解析 Bing / Baidu 搜索结果页中的链接。"""
    from crawler.boards import is_relevant_for_crawl

    soup = BeautifulSoup(html, "lxml")
    results: list[ParsedAnnouncement] = []
    seen: set[str] = set()

    selectors = [
        ("li.b_algo h2 a", "li.b_algo"),
        ("#b_results .b_algo h2 a", "#b_results .b_algo"),
        ("div.result h3 a", "div.result"),
        ("div.c-container h3 a", "div.c-container"),
        ("div.result-op h3 a", "div.result-op"),
    ]

    for link_sel, box_sel in selectors:
        for a in soup.select(link_sel):
            title = a.get_text(strip=True)
            if len(title) < 8 or len(title) > 200:
                continue

            if board and phase:
                if not is_relevant_for_crawl(title, board, phase, summary=snippet):
                    continue
            elif not is_relevant(title, college_type):
                continue

            href = a.get("href", "")
            if not href:
                continue

            parent = a.find_parent(["li", "div"])
            snippet = ""
            if parent:
                p = parent.select_one("p, .b_caption p, .c-abstract")
                if p:
                    snippet = p.get_text(" ", strip=True)

            url = href.split("#")[0]
            if not is_valid_announcement_url(url):
                continue
            if url in seen:
                continue
            seen.add(url)

            publish_date, deadline, event_time, event_format = enrich_dates(title, snippet)
            event_type = detect_event_type(title)
            status = detect_status(title, deadline)

            if not is_year_eligible(title, publish_date, deadline):
                continue

            results.append(ParsedAnnouncement(
                title=title,
                url=url,
                publish_date=publish_date,
                deadline=deadline,
                event_time=event_time,
                event_format=event_format,
                event_type=event_type,
                status=status,
                summary=snippet[:300] if snippet else None,
                source=SEARCH_LABEL,
            ))

    return results


def parse_wechat_search_results(
    html: str,
    target,
    base_url: str,
    *,
    board: str | None = None,
    phase: str | None = None,
) -> list[ParsedAnnouncement]:
    from crawler.boards import is_relevant_for_crawl

    soup = BeautifulSoup(html, "lxml")
    results: list[ParsedAnnouncement] = []
    seen: set[str] = set()

    for box in soup.select("ul.news-list li, div.txt-box, div.news-box, li, .news-box"):
        title_el = box.select_one("h3 a, h4 a, a[uigs], a[target='_blank'], .tit a")
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        if len(title) < 6:
            continue

        href = title_el.get("href", "")
        url = normalize_url(href, base_url)
        if not url or url in seen:
            continue

        snippet_el = box.select_one("p.txt-info, p.content, .txt-info")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""

        if board and phase:
            if not is_relevant_for_crawl(title, board, phase, summary=snippet):
                continue
        elif not is_relevant(title, target.college_type, from_wechat=True):
            continue

        seen.add(url)
        time_el = box.select_one("span.s3, .s-p span:last-child, .time")
        time_text = time_el.get_text(strip=True) if time_el else ""
        combined = f"{title} {snippet}"
        if time_text:
            notice_pub = extract_notice_publish_date(time_text)
        else:
            notice_pub = None
        publish_date, deadline, event_time, event_format = enrich_dates(title, snippet)
        if not publish_date and notice_pub:
            publish_date = notice_pub
        event_type = detect_event_type(title)
        status = detect_status(title, deadline)

        if not is_year_eligible(title, publish_date, deadline):
            continue

        results.append(ParsedAnnouncement(
            title=title,
            url=url,
            publish_date=publish_date,
            deadline=deadline,
            event_time=event_time,
            event_format=event_format,
            event_type=event_type,
            status=status,
            summary=snippet[:300] if snippet else None,
            source=WECHAT_LABEL,
            university=target.university,
            college=target.college,
            college_type=target.college_type,
        ))

    return results
