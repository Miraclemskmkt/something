"""从通知页、附件、报名链接聚合全文，供 LLM / 正则抽取。"""

from __future__ import annotations



import logging

import re

from dataclasses import dataclass, field

from urllib.parse import urljoin



import httpx

from bs4 import BeautifulSoup



from config import settings

from crawler.attachment_extractor import fetch_attachment_text, is_attachment_url

from crawler.fetcher import fetch_page

from crawler.parser import extract_page_text, extract_table_text

from crawler.url_quality import is_pdf_url



logger = logging.getLogger(__name__)



ATTACH_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx)(?:\?|$)", re.I)

REG_LINK_HINTS = ("报名", "申请", "register", "apply", "summer", "xly", "signup", "bmxt")





@dataclass

class NoticeContentBundle:

    text: str = ""

    table_text: str = ""

    table_html: str = ""

    raw_html: str = ""

    garbled: bool = False

    sources: list[str] = field(default_factory=list)





async def _fetch_text(url: str, client: httpx.AsyncClient, *, title: str = "") -> str:

    if is_attachment_url(url):

        text = await fetch_attachment_text(url, client=client, title=title)

        return text or ""

    raw = await fetch_page(url, client=client, fast=True)

    if not raw:

        return ""

    if is_pdf_url(url) or raw.strip().startswith("%PDF"):

        from crawler.attachment_extractor import extract_text_from_bytes



        data = raw.encode("latin-1") if isinstance(raw, str) else raw

        return extract_text_from_bytes(data, url) or raw

    return extract_page_text(raw, title=title) if "<" in raw[:500] else raw





def _extract_table_html(html: str) -> str:

    if not html or "<table" not in html.lower():

        return ""

    soup = BeautifulSoup(html, "lxml")

    parts: list[str] = []

    for table in soup.find_all("table")[:3]:

        parts.append(str(table)[:6000])

    return "\n".join(parts)





def _attachment_urls(html: str, base_url: str, limit: int = 3) -> list[str]:

    if not html or "<" not in html[:500]:

        return []

    soup = BeautifulSoup(html, "lxml")

    out: list[str] = []

    seen: set[str] = set()

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if not ATTACH_RE.search(href) and "pdf" not in href.lower():

            continue

        url = urljoin(base_url, href)

        if url in seen:

            continue

        seen.add(url)

        out.append(url)

        if len(out) >= limit:

            break

    return out





def _registration_urls(html: str, base_url: str, limit: int = 2) -> list[str]:

    if not html or "<" not in html[:500]:

        return []

    soup = BeautifulSoup(html, "lxml")

    out: list[str] = []

    seen: set[str] = set()

    for a in soup.find_all("a", href=True):

        href = a["href"]

        label = (a.get_text(strip=True) or "") + " " + href

        if not any(h in label.lower() for h in REG_LINK_HINTS):

            continue

        url = urljoin(base_url, href)

        if not url.startswith("http") or url in seen:

            continue

        seen.add(url)

        out.append(url)

        if len(out) >= limit:

            break

    return out





async def gather_notice_content(

    url: str,

    *,

    client: httpx.AsyncClient | None = None,

    title: str = "",

) -> str:

    bundle = await gather_notice_content_bundle(url, client=client, title=title)

    return bundle.text





async def gather_notice_content_bundle(

    url: str,

    *,

    client: httpx.AsyncClient | None = None,

    title: str = "",

) -> NoticeContentBundle:

    """网页正文 + 表格 + 附件，结构化返回供 LLM 分类/抽取。"""

    from crawler.fetcher import create_http_client

    from crawler.llm_extractor import is_likely_garbled



    async def _run(c: httpx.AsyncClient) -> NoticeContentBundle:

        bundle = NoticeContentBundle()
        page_url = url

        parts: list[str] = []

        if title:

            parts.append(f"标题：{title}")



        from crawler.wechat_article import fetch_weixin_article, is_weixin_url

        if is_weixin_url(page_url):

            final, wx_title, wx_text, wx_html = await fetch_weixin_article(page_url, client=c)

            if final:

                page_url = final

            if wx_title:

                parts.append(f"标题：{wx_title}")

            if wx_text:

                parts.append(wx_text[:12000])

                bundle.sources.append("weixin")

            bundle.raw_html = wx_html or ""

            bundle.text = "\n\n".join(parts)[:25000]

            bundle.garbled = is_likely_garbled(bundle.text)

            return bundle



        if is_attachment_url(page_url):

            att_text = await fetch_attachment_text(page_url, client=c, title=title)

            if att_text:

                parts.append(att_text[:12000])

                bundle.sources.append("attachment_url")

            bundle.text = "\n\n".join(parts)[:25000]

            bundle.garbled = is_likely_garbled(bundle.text)

            return bundle



        page_raw = await fetch_page(page_url, client=c, fast=False)

        if not page_raw:

            bundle.text = title or ""

            return bundle



        bundle.raw_html = page_raw if "<" in page_raw[:500] else ""



        if bundle.raw_html:

            bundle.table_html = _extract_table_html(page_raw)

            bundle.table_text = extract_table_text(page_raw)



        main_text = extract_page_text(page_raw, title=title) if bundle.raw_html else page_raw

        if main_text:

            parts.append(main_text[:12000])

            bundle.sources.append("main_page")



        if bundle.raw_html:

            if bundle.table_text and bundle.table_text not in main_text:

                parts.append(f"[表格]\n{bundle.table_text[:6000]}")



            for att in _attachment_urls(page_raw, page_url):

                try:

                    att_text = await _fetch_text(att, c, title=title)

                    if att_text and len(att_text) > 80:

                        parts.append(f"[附件 {att}]\n{att_text[:8000]}")

                        bundle.sources.append(f"attach:{att[:40]}")

                except Exception as e:

                    logger.debug("附件读取失败 %s: %s", att[:60], e)



            for reg in _registration_urls(page_raw, page_url):

                try:

                    reg_text = await _fetch_text(reg, c, title=title)

                    if reg_text:

                        parts.append(f"[报名页 {reg}]\n{reg_text[:4000]}")

                        bundle.sources.append("registration")

                except Exception:

                    pass



        bundle.text = "\n\n".join(parts)[:25000]

        bundle.garbled = is_likely_garbled(bundle.text)



        if bundle.garbled and settings.llm_multimodal_enabled:

            mm_text = await _try_multimodal_pdf(page_url, c, title=title)

            if mm_text:

                bundle.text = f"{bundle.text}\n\n[多模态识别]\n{mm_text}"[:25000]

                bundle.garbled = False

                bundle.sources.append("multimodal")



        return bundle



    if client is not None:

        return await _run(client)

    async with create_http_client() as c:

        return await _run(c)





async def _try_multimodal_pdf(

    url: str,

    client: httpx.AsyncClient,

    *,

    title: str = "",

    max_pages: int = 3,

) -> str:

    """扫描件 PDF：渲染前几页为图，送多模态 LLM（可选，需 pymupdf）。"""

    if not is_attachment_url(url) and not is_pdf_url(url):

        return ""

    try:

        import base64

        import fitz  # pymupdf

    except ImportError:

        logger.debug("multimodal 需要 pymupdf，跳过: %s", url[:60])

        return ""



    from crawler.anti_crawl import browser_headers, site_root

    from crawler.llm_client import call_llm_chat



    try:

        resp = await client.get(

            url,

            headers=browser_headers(url, referer=site_root(url)),

            follow_redirects=True,

        )

        if resp.status_code != 200:

            return ""

        doc = fitz.open(stream=resp.content, filetype="pdf")

        images_b64: list[str] = []

        for i in range(min(max_pages, len(doc))):

            pix = doc[i].get_pixmap(dpi=150)

            images_b64.append(base64.b64encode(pix.tobytes("png")).decode())

        doc.close()

        if not images_b64:

            return ""



        prompt = (

            f"以下是保研夏令营通知 PDF 扫描页。请提取报名开始、截止、举办时间、举办形式。\n"

            f"标题：{title[:100]}\n仅返回 JSON："

            '{"publish_date":null,"deadline":null,"event_start":null,"event_end":null,"event_format":null}'

        )

        raw, err = call_llm_chat(prompt, model=settings.llm_multimodal_model, timeout=120)

        return raw or "" if not err else ""

    except Exception as e:

        logger.debug("multimodal PDF failed: %s", e)

        return ""

