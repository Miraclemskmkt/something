"""LLM 噪音分类器：正文抓取后判断是否为保研/预推免招生通知（YES/NO）。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from config import settings
from crawler.llm_client import call_llm_chat
from crawler.noise_filter import record_llm_learned_noise

logger = logging.getLogger(__name__)

# 标题含以下词且非 SKIP 类帖子时，1.5b 误判 NO 仍放行
_NOTICE_TITLE_HINTS = (
    "报名通知", "招生通知", "招生简章", "夏令营通知", "推免招生",
    "接收推荐", "预推免通知", "开放日通知", "暑期营通知",
)


def _title_looks_like_official_notice(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    skip = ("求问", "请问", "求助", "经验", "蹲", "有没有", "怎么", "真题", "汇总帖")
    if any(k in t for k in skip):
        return False
    return any(k in t for k in _NOTICE_TITLE_HINTS)

CLASSIFY_PROMPT = """判断以下通知是否属于国内高校法学院或外国语/外语学院面向本科生的保研夏令营或预推免招生通知。
仅回复 YES 或 NO，不要解释。

通知标题：{title}
正文前500字：
{content}
"""

FORUM_CLASSIFY_PROMPT = """判断论坛帖子是否是「法学院或外国语/外语学院面向本科生的保研夏令营或预推免招生通知」。
仅回复 YES 或 NO，不要解释。

示例1
标题：求北外法语夏令营经验
正文：有没有学长学姐分享一下面试经验
答案：NO

示例2
标题：北外法语学院2026年夏令营报名通知
正文：外国语学院将于7月举办夏令营，报名截止6月30日
答案：YES

示例3
标题：复旦大学法学院2026年推免预报名通知
正文：我院现开展推免生接收工作，详见官网
答案：YES

以下类型必须回答 NO：经验分享、求定位、问答讨论、面试经验、真题汇总、求助、蹲学长学姐。

标题：{title}
正文：
{content}
答案："""

@dataclass
class ClassifyResult:
    relevant: bool
    raw: str = ""
    failure_type: str | None = None  # success | disabled | timeout | error | ambiguous
    detail: str = ""


def _parse_yes_no(raw: str) -> bool | None:
    if not raw:
        return None
    t = raw.strip().upper()
    if re.search(r"\bYES\b", t) or t == "是" or t.startswith("YES"):
        return True
    if re.search(r"\bNO\b", t) or t == "否" or t.startswith("NO"):
        return False
    if "不是" in raw or "不属于" in raw:
        return False
    if "属于" in raw and "不" not in raw[: raw.index("属于")]:
        return True
    return None


def classify_notice_relevance(
    title: str,
    content: str,
    *,
    college_type: str = "law",
    url: str = "",
    auto_learn: bool = True,
) -> ClassifyResult:
    """
    LLM 相关性分类。规则过滤（title_filter）已通过后才调用。
    若 LLM 未启用或分类未启用，默认 relevant=True（交给规则底牌）。
    """
    if not settings.llm_enabled or not settings.llm_classify_enabled:
        return ClassifyResult(relevant=True, failure_type="disabled")

    snippet = (content or "").strip()[:500]
    if len(snippet) < 40:
        return ClassifyResult(relevant=True, failure_type="no_content", detail="正文过短，跳过分类")

    prompt = CLASSIFY_PROMPT.format(title=(title or "")[:200], content=snippet)
    raw, err = call_llm_chat(
        prompt,
        model=settings.llm_classify_model,
        timeout=settings.llm_classify_timeout_sec,
        temperature=0.0,
    )

    if err:
        ft = "timeout" if err == "timeout" else "error"
        logger.debug("LLM 分类失败，放行由规则兜底: %s", err)
        return ClassifyResult(relevant=True, failure_type=ft, detail=err)

    verdict = _parse_yes_no(raw or "")
    if verdict is None:
        logger.debug("LLM 分类歧义 (%s)，放行: %s", raw[:30], (title or "")[:40])
        return ClassifyResult(relevant=True, failure_type="ambiguous", raw=raw or "")

    if not verdict and auto_learn:
        record_llm_learned_noise(
            title or "",
            snippet[:200],
            reason=f"llm_classifier:{raw[:20]}",
            url=url,
        )

    if verdict:
        logger.debug("LLM 分类 YES: %s", (title or "")[:50])
    else:
        logger.info("LLM 分类 NO，丢弃: %s", (title or "")[:60])

    return ClassifyResult(relevant=verdict, failure_type="success", raw=raw or "")


def classify_forum_post(
    title: str,
    content: str,
    *,
    url: str = "",
    auto_learn: bool = True,
) -> ClassifyResult:
    """
    论坛帖入库前 LLM 分类（1.5b，严格模式）。
    NO / 歧义 → 丢弃；仅 YES 进入后续流程。
    """
    if not settings.llm_enabled or not settings.llm_forum_classify_enabled:
        return ClassifyResult(relevant=True, failure_type="disabled")

    title = (title or "").strip()
    snippet = (content or "").strip()[:300]
    body = snippet if snippet else title
    if len(body) < 8:
        return ClassifyResult(relevant=False, failure_type="no_content", detail="标题过短")

    prompt = FORUM_CLASSIFY_PROMPT.format(title=title[:200], content=body)
    raw, err = call_llm_chat(
        prompt,
        model=settings.llm_classify_model,
        timeout=settings.llm_classify_timeout_sec,
        temperature=0.0,
    )
    if err:
        logger.debug("论坛 LLM 分类失败，规则兜底: %s", err)
        return ClassifyResult(relevant=True, failure_type=err if err == "timeout" else "error", detail=err)

    verdict = _parse_yes_no(raw or "")
    if verdict is None:
        logger.info("论坛 LLM 分类歧义，丢弃: %s", title[:60])
        return ClassifyResult(relevant=False, failure_type="ambiguous", raw=raw or "")

    if not verdict and _title_looks_like_official_notice(title):
        logger.info("论坛 LLM 判 NO 但标题像官方通知，放行: %s", title[:60])
        return ClassifyResult(relevant=True, failure_type="title_override", raw=raw or "")

    if not verdict and auto_learn:
        record_llm_learned_noise(title, body[:200], reason=f"forum_classifier:{raw[:20]}", url=url)

    if verdict:
        logger.debug("论坛 LLM YES: %s", title[:50])
    else:
        logger.info("论坛 LLM NO，丢弃: %s", title[:60])

    return ClassifyResult(relevant=verdict, failure_type="success", raw=raw or "")
