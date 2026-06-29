"""汇总列表页识别，并从中选出最匹配的通知详情链接。"""
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from crawler.parser import ParsedAnnouncement, is_valid_announcement_url, normalize_url, parse_news_list

# URL 特征：多为公示/列表入口，而非单篇通知
LISTING_URL_RE = re.compile(
    r"(?:xlygs|xlygl|/list(?:/|\.|$)|default\.aspx|index\.htm?$|/xxgs/|"
    r"/tzgg/index|/notice/?$|/news/?$|/info/?$)",
    re.I,
)

LISTING_TITLE_HINTS = ("公示", "列表", "汇总", "一览", "通知公告", "夏令营管理", "招生简章")

# 详情页常见路径
DETAIL_URL_RE = re.compile(r"/info/\d+/\d+\.(?:htm|html|shtml)|/View/\d+|/Details/|/page\.htm", re.I)


def is_listing_url(url: str) -> bool:
    if not url:
        return False
    if DETAIL_URL_RE.search(url):
        return False
    return bool(LISTING_URL_RE.search(url))


def is_listing_page(html: str, url: str, title: str = "") -> bool:
    if is_listing_url(url):
        return True
    if title and any(h in title for h in LISTING_TITLE_HINTS) and not DETAIL_URL_RE.search(url):
        return True
    if not html:
        return False
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    if len(text) > 8000:
        return False
    camp_links = 0
    for a in soup.find_all("a", href=True):
        label = a.get_text(strip=True)
        href = a["href"]
        if len(label) < 6:
            continue
        if any(k in label for k in ("夏令营", "预推免", "优营", "暑期")):
            if is_valid_announcement_url(normalize_url(href, url)):
                camp_links += 1
    return camp_links >= 3


def _title_score(candidate: str, ref: str, university: str, college: str) -> float:
    score = 0.0
    if ref and ref[:12] in candidate:
        score += 5.0
    if university and university[:2] in candidate:
        score += 2.0
    if college and college[:2] in candidate:
        score += 2.0
    if ref:
        score += SequenceMatcher(None, ref[:30], candidate[:30]).ratio() * 3.0
    if "2026" in candidate or "2025" in candidate:
        score += 0.5
    return score


def pick_detail_from_listing(
    html: str,
    page_url: str,
    *,
    title: str = "",
    university: str = "",
    college: str = "",
    college_type: str = "law",
    board: str | None = None,
    phase: str | None = None,
) -> ParsedAnnouncement | None:
    """从可访问的列表页中选出与当前通知最匹配的详情条目。"""
    base = page_url
    parsed = urlparse(page_url)
    if parsed.scheme and parsed.netloc:
        base = f"{parsed.scheme}://{parsed.netloc}"

    items = parse_news_list(html, page_url, college_type, board=board, phase=phase)
    if not items:
        return None

    scored = sorted(
        ((_title_score(it.title, title, university, college), it) for it in items),
        key=lambda x: -x[0],
    )
    best_score, best = scored[0]
    if best_score >= 2.0 or len(items) == 1:
        best.university = university or best.university
        best.college = college or best.college
        best.college_type = college_type
        return best
    return None
