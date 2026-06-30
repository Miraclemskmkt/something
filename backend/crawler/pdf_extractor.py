"""PDF 通知正文提取：下载 bytes → 解析文本 → 供时间/形式字段补全。"""
import io
import logging
import re

import httpx

from config import settings
from crawler.anti_crawl import browser_headers, site_root
from crawler.parser import compact_spaced_text

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore


def looks_like_html(content: str) -> bool:
    if not content:
        return False
    return bool(re.search(r"<[a-zA-Z!/?]", content[:4000]))


def extract_text_from_pdf_bytes(data: bytes) -> str:
    if not data or PdfReader is None:
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
        return "\n".join(parts)
    except Exception as e:
        logger.debug("PDF parse error: %s", e)
        return ""


def extract_title_from_document_text(text: str) -> str:
    """从 PDF/纯文本首段推断通知标题。"""
    if not text:
        return ""
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line.strip())
        if len(line) < 8:
            continue
        if any(k in line for k in ("夏令营", "预推免", "推免", "开放日", "暑期")):
            return line[:500]
    chunk = compact_spaced_text(text[:800])
    m = re.search(r"[^\n。；]{8,80}(?:夏令营|预推免)[^\n。；]{0,40}", chunk)
    return m.group(0).strip()[:500] if m else ""


async def fetch_pdf_text(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    if not settings.pdf_enabled or PdfReader is None:
        return None
    if not url or not url.startswith("http"):
        return None

    async def _do(c: httpx.AsyncClient) -> str | None:
        try:
            root = site_root(url)
            resp = await c.get(
                url,
                headers=browser_headers(url, referer=root),
                follow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug("PDF fetch status %s: %s", resp.status_code, url[:80])
                return None
            data = resp.content
            if not data or len(data) > settings.pdf_max_bytes:
                logger.debug("PDF too large or empty: %s", url[:80])
                return None
            ct = (resp.headers.get("content-type") or "").lower()
            if "pdf" not in ct and not url.lower().split("?")[0].endswith(".pdf"):
                if not data[:5].startswith(b"%PDF-"):
                    return None
            raw = extract_text_from_pdf_bytes(data)
            if not raw or len(raw.strip()) < 50:
                logger.debug("PDF text too short: %s", url[:80])
                return None
            text = compact_spaced_text(raw)
            logger.info("PDF 正文提取成功 (%d 字): %s", len(text), url[:70])
            return text
        except Exception as e:
            logger.debug("PDF fetch error %s: %s", url[:80], e)
            return None

    if client is not None:
        return await _do(client)
    from crawler.anti_crawl import create_http_client

    async with create_http_client() as c:
        return await _do(c)
