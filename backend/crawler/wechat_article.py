"""微信公众号文章正文抓取、快照与指纹去重。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from config import settings

logger = logging.getLogger(__name__)

SOGOU_REFERER = "https://weixin.sogou.com/"
SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "wechat_snapshots"


def is_weixin_url(url: str) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return "mp.weixin.qq.com" in host and "/s" in urlparse(url).path


def weixin_fingerprint(url: str) -> str:
    """微信文章 s/xxxx 唯一指纹。"""
    m = re.search(r"/s/([A-Za-z0-9_-]+)", url or "")
    if m:
        return m.group(1)
    return hashlib.sha256((url or "").encode()).hexdigest()[:16]


def _weixin_headers() -> dict[str, str]:
    return {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": SOGOU_REFERER,
    }


def _strip_hidden(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(style=True):
        if not getattr(tag, "attrs", None):
            continue
        style = (tag.get("style") or "").replace(" ", "").lower()
        if "visibility:hidden" in style or "display:none" in style:
            tag.decompose()
    for tag in soup.select('[style*="visibility: hidden"], [style*="visibility:hidden"]'):
        tag.decompose()


def _title_from_raw_html(html: str) -> str:
    """从微信页内嵌变量 / og 标签提取标题（不依赖 js_content 渲染）。"""
    if not html:
        return ""
    for pat in (
        r"var msg_title\s*=\s*'([^']+)'",
        r'var msg_title\s*=\s*"([^"]+)"',
        r'property="og:title"\s+content="([^"]+)"',
    ):
        m = re.search(pat, html)
        if m:
            t = m.group(1).strip()
            if len(t) >= 4:
                return t[:500]
    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()[:500]
    return ""


def _text_from_raw_html(html: str) -> str:
    """从 msg_desc / js_content 提取正文。"""
    if not html:
        return ""
    for pat in (
        r"var msg_desc\s*=\s*'([^']*)'",
        r'var msg_desc\s*=\s*"([^"]*)"',
    ):
        m = re.search(pat, html)
        if m:
            t = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
            if len(t) >= 20:
                return t[:25000]
    soup = BeautifulSoup(html, "lxml")
    content_el = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    if content_el:
        return content_el.get_text("\n", strip=True)[:25000]
    return ""


def extract_weixin_article_html(html: str) -> tuple[str, str, str]:
    """
    从微信文章页提取标题、正文纯文本、原始 js_content HTML。
    返回 (title, text, content_html)
    """
    if not html:
        return "", "", ""

    title = _title_from_raw_html(html)
    text = _text_from_raw_html(html)

    soup = BeautifulSoup(html, "lxml")
    try:
        _strip_hidden(soup)
    except Exception:
        pass

    if not title:
        for sel in ("#activity-name", "h1.rich_media_title", "h1", "h2"):
            el = soup.select_one(sel)
            if el:
                title = el.get_text(strip=True)
                if len(title) >= 4:
                    break
    if not title and soup.title and soup.title.string:
        title = soup.title.string.split("-")[0].strip()

    content_el = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    content_html = str(content_el) if content_el else ""
    if not text and content_el:
        text = content_el.get_text("\n", strip=True)

    if len(text) < 80:
        body = soup.find("body")
        if body:
            text = body.get_text("\n", strip=True)[:12000]

    return title[:500], text[:25000], content_html[:50000]


def save_weixin_snapshot(url: str, title: str, text: str, html: str = "") -> Path | None:
    if not settings.wechat_snapshot_enabled:
        return None
    fp = weixin_fingerprint(url)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{fp}.json"
    payload = {
        "url": url,
        "title": title,
        "text": text[:50000],
        "html_len": len(html),
        "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def fetch_weixin_article(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, str, str, str]:
    """
    抓取微信文章。返回 (final_url, title, text, raw_html)。
    遇「环境异常」返回空文本。
    """
    if not is_weixin_url(url):
        return url, "", "", ""

    async def _do(c: httpx.AsyncClient) -> tuple[str, str, str, str]:
        try:
            resp = await c.get(url, headers=_weixin_headers(), follow_redirects=True)
            final = str(resp.url).split("#")[0]
            html = resp.text or ""
            if "环境异常" in html or ("verify" in html.lower() and len(html) < 8000):
                logger.warning("微信文章环境验证: %s", final[:70])
                return final, "", "", html
            title, text, content_html = extract_weixin_article_html(html)
            if not title:
                title = _title_from_raw_html(html)
            if not text:
                text = _text_from_raw_html(html)
            save_weixin_snapshot(final, title, text, content_html)
            return final, title, text, html
        except Exception as e:
            logger.debug("微信文章抓取失败 %s: %s", url[:60], e)
            return url, "", "", ""

    if client is not None:
        return await _do(client)
    async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as c:
        return await _do(c)


def is_poster_only(text: str) -> bool:
    """正文极短，可能为图片海报。"""
    return len((text or "").strip()) < settings.wechat_min_text_len
