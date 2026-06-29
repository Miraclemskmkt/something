"""校验检索结果是否来自高校官方权威域名或通知路径。"""

import logging
from urllib.parse import urlparse

from double_first_class import DOUBLE_FIRST_CLASS_UNIVERSITIES

logger = logging.getLogger(__name__)

WECHAT_HOST = "mp.weixin.qq.com"
OFFICIAL_SUFFIXES = (".edu.cn", ".ac.cn")

NOTICE_PATH_HINTS = (
    "tzgg", "xwzx", "notice", "info", "list", "news", "gg",
    "announcement", "zsgz", "yjs", "graduate", "postgraduate",
    "xygg", "gonggao", "article", "content", "show",
)

CAMP_TITLE_HINTS = ("夏令营", "预推免", "推免", "暑期", "优营", "开放日")


def _root_domain(host: str) -> str:
    host = host.lower().replace("www.", "")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("edu", "ac") and parts[-1] == "cn":
        return ".".join(parts[-3:])
    return host


def _build_university_roots() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for uni in DOUBLE_FIRST_CLASS_UNIVERSITIES:
        host = urlparse(uni.url).netloc.lower().replace("www.", "")
        mapping[uni.name] = _root_domain(host)
    return mapping


UNIVERSITY_ROOT_DOMAINS = _build_university_roots()

SHORT_NAME_HINTS: dict[str, list[str]] = {
    "北京大学": ["北大", "pku"],
    "清华大学": ["清华", "tsinghua"],
    "中国人民大学": ["人大", "ruc"],
    "复旦大学": ["复旦", "fudan"],
    "武汉大学": ["武大", "whu"],
    "南京大学": ["南大", "nju"],
    "浙江大学": ["浙大", "zju"],
    "上海交通大学": ["上交", "sjtu"],
    "中山大学": ["中大", "sysu"],
    "厦门大学": ["厦大", "xmu"],
    "四川大学": ["川大", "scu"],
    "南开大学": ["南开", "nankai"],
    "天津大学": ["天大", "tju"],
    "同济大学": ["同济", "tongji"],
    "华东师范大学": ["华师大", "ecnu"],
    "北京师范大学": ["北师大", "bnu"],
    "中国政法大学": ["法大", "cupl"],
    "对外经济贸易大学": ["贸大", "uibe"],
    "北京外国语大学": ["北外", "bfsu"],
    "上海外国语大学": ["上外", "shisu"],
}


def is_official_host(host: str) -> bool:
    h = host.lower().replace("www.", "")
    if WECHAT_HOST in h:
        return True
    return any(h.endswith(suffix) or h == suffix.lstrip(".") for suffix in OFFICIAL_SUFFIXES)


def has_official_path(path: str) -> bool:
    p = path.lower()
    return any(hint in p for hint in NOTICE_PATH_HINTS)


def _title_matches_university(title: str, university: str) -> bool:
    if university in title:
        return True
    hints = SHORT_NAME_HINTS.get(university, [])
    return any(h in title.lower() or h in title for h in hints)


def _title_matches_college(title: str, college: str) -> bool:
    if college in title:
        return True
    short = college.replace("学院", "").replace("大学", "")
    return len(short) >= 2 and short in title


def _domains_for_target(target) -> set[str]:
    from crawler.grad_school_domains import grad_domain_candidates

    roots: set[str] = set()
    uni_root = UNIVERSITY_ROOT_DOMAINS.get(target.university)
    if uni_root:
        roots.add(uni_root)
    if target.base_url:
        host = urlparse(target.base_url).netloc.lower().replace("www.", "")
        roots.add(host)
        roots.add(_root_domain(host))
    for g in grad_domain_candidates(target.university, uni_root or ""):
        roots.add(g)
    return roots


def host_matches_target(host: str, target) -> bool:
    h = host.lower().replace("www.", "")
    for domain in _domains_for_target(target):
        if h == domain or h.endswith("." + domain):
            return True
    return False


def classify_source(url: str, target) -> str:
    host = urlparse(url).netloc.lower()
    if WECHAT_HOST in host:
        return "微信公众号"
    if target.base_url:
        college_host = urlparse(target.base_url).netloc.lower().replace("www.", "")
        current = host.replace("www.", "")
        if current == college_host or current.endswith("." + college_host):
            return "学院官网"
    if host_matches_target(host, target):
        return "学校官网"
    return "官方权威"


def verify_official_url(
    url: str,
    title: str,
    target,
    summary: str | None = None,
) -> tuple[bool, str]:
    """
    校验 URL 是否为该高校官方权威来源。
    返回 (是否通过, 来源标签)。
    """
    if not url or not url.startswith("http"):
        return False, ""

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not is_official_host(host):
        return False, ""

    combined = f"{title} {summary or ''}"

    if WECHAT_HOST in host:
        if _title_matches_university(combined, target.university):
            return True, "微信公众号"
        if _title_matches_college(combined, target.college):
            return True, "微信公众号"
        return False, ""

    if host_matches_target(host, target):
        return True, classify_source(url, target)

    if _title_matches_university(combined, target.university):
        if has_official_path(parsed.path) or any(k in title for k in CAMP_TITLE_HINTS):
            return True, classify_source(url, target)

    return False, ""


async def resolve_final_url(url: str, client) -> str:
    """跟随重定向得到最终 URL（Bing/Baidu 跳转链）。"""
    try:
        resp = await client.head(url, follow_redirects=True)
        return str(resp.url).split("#")[0]
    except Exception:
        try:
            resp = await client.get(url, follow_redirects=True)
            return str(resp.url).split("#")[0]
        except Exception as e:
            logger.debug("Resolve URL failed %s: %s", url, e)
            return url.split("#")[0]
