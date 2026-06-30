"""LLM 从论坛帖正文提取官方通知链接（edu.cn / 微信公众号）。"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

from config import settings
from crawler.llm_client import call_llm_chat

logger = logging.getLogger(__name__)

_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+?(?:edu\.cn|ac\.cn|mp\.weixin\.qq\.com/s/[A-Za-z0-9_-]+)[^\s\"'<>]*",
    re.I,
)

LINK_EXTRACT_PROMPT = """从以下文本中提取所有「高校官方招生通知」链接。
只保留：以 .edu.cn 或 .ac.cn 结尾的官网链接，或 mp.weixin.qq.com/s/ 开头的微信公众号文章。
忽略论坛、百度、知乎、附件下载站等链接。
如果没有找到，返回空数组 []。
仅返回 JSON 数组，不要 markdown，不要解释。

标题：{title}

文本：
{content}
"""


def _valid_notice_url(url: str) -> bool:
    u = (url or "").strip().split("#")[0]
    if not u.startswith("http"):
        return False
    host = urlparse(u).netloc.lower()
    if "mp.weixin.qq.com" in host and "/s/" in u:
        return True
    return host.endswith(".edu.cn") or host.endswith(".ac.cn")


def _parse_url_array(raw: str) -> list[str]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str) and _valid_notice_url(item):
            out.append(item.split("#")[0].strip())
    return out


def _extract_urls_regex(content: str) -> list[str]:
    found: list[str] = []
    for m in _URL_RE.finditer(content or ""):
        u = m.group(0).rstrip(".,;)]}\"'")
        if _valid_notice_url(u):
            found.append(u.split("#")[0].strip())
    return list(dict.fromkeys(found))


def extract_notice_urls(
    content: str,
    *,
    title: str = "",
    max_chars: int = 2500,
) -> list[str]:
    """正则 + LLM 合并提取官方/微信通知 URL。"""
    combined = f"{title}\n{content or ''}"[:max_chars]
    regex_urls = _extract_urls_regex(combined)
    llm_urls = extract_notice_urls_llm(content, title=title, max_chars=max_chars)
    merged = list(dict.fromkeys(regex_urls + llm_urls))
    if merged and not llm_urls and regex_urls:
        logger.debug("链接提取：正则命中 %d 条", len(regex_urls))
    return merged


def extract_notice_urls_llm(
    content: str,
    *,
    title: str = "",
    max_chars: int = 2500,
) -> list[str]:
    """用 7b 模型从帖子正文提取官方/微信通知 URL。失败返回空列表。"""
    if not settings.llm_enabled or not settings.llm_link_extract_enabled:
        return []
    snippet = (content or "").strip()
    if not snippet and not title:
        return []
    combined = f"{title}\n{snippet}"[:max_chars]
    if len(combined.strip()) < 15:
        return []

    prompt = LINK_EXTRACT_PROMPT.format(title=(title or "")[:200], content=combined)
    raw, err = call_llm_chat(prompt, temperature=0.0, timeout=min(settings.llm_timeout_sec, 90))
    if err or not raw:
        logger.debug("LLM 链接提取失败: %s", err)
        return []

    urls = _parse_url_array(raw)
    if urls:
        logger.info("LLM 提取到 %d 个官方链接: %s", len(urls), urls[0][:70])
    return urls
