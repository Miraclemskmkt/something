"""大模型结构化抽取 + 噪音分类辅助：LLM 主力，正则兜底。"""

from __future__ import annotations



import json

import logging

import re

from dataclasses import dataclass

from typing import Any



from config import settings

from crawler.llm_cache import (

    compute_field_confidence,

    format_few_shots_for_prompt,

    get_cached_extract,

    set_cached_extract,

)

from crawler.llm_client import call_llm_chat



logger = logging.getLogger(__name__)



_KEYWORDS = (

    "截止", "报名", "申请", "开放", "举办", "夏令营", "预推免",

    "线上", "线下", "混合", "腾讯会议", "Zoom", "报到", "活动时间",

)



EXTRACT_PROMPT = """你是保研夏令营通知信息抽取助手。从通知文本中提取报名与举办信息。

若无明确信息填 null，不要猜测。



{few_shots}



仅返回 JSON，不要 markdown，不要解释：

{{"publish_date":null,"deadline":null,"event_start":null,"event_end":null,"event_format":null}}



字段说明：

- publish_date: 报名/申请开始时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS

- deadline: 报名/申请截止时间

- event_start / event_end: 夏令营举办起止日期

- event_format: 只能是「线上」「线下」「线上线下」之一



通知标题：{title}



文本：

{content}

"""



TABLE_EXTRACT_PROMPT = """以下是一则夏令营通知中的 HTML 表格或结构化表格文本。请理解表格结构并提取报名与举办信息。

若无明确信息填 null。



仅返回 JSON：

{{"publish_date":null,"deadline":null,"event_start":null,"event_end":null,"event_format":null}}



标题：{title}



表格内容：

{table}

"""



GARBLED_EXTRACT_PROMPT = """以下文本来自 PDF 扫描件或乱码提取，可能不完整。请尽力从中提取夏令营报名时间与举办信息。

无法确定的字段填 null。



仅返回 JSON：

{{"publish_date":null,"deadline":null,"event_start":null,"event_end":null,"event_format":null}}



标题：{title}



文本：

{content}

"""

ENRICH_ALL_PROMPT = """你是保研夏令营通知结构化抽取助手。从通知文本中提取全部可用信息，无则填 null，不要猜测。

{few_shots}

仅返回 JSON，不要 markdown：

{{"publish_date":null,"deadline":null,"event_start":null,"event_end":null,"event_format":null,"requirements":null,"majors":null,"apply_url":null,"materials":null,"contact":null,"excellent_ratio":null}}

字段说明：
- publish_date / deadline / event_start / event_end / event_format：同常规夏令营通知
- requirements：申请条件（排名、英语、专业等，一句话概括）
- majors：招收专业或方向
- apply_url：网上报名或系统链接
- materials：材料清单（简要）
- contact：联系方式（邮箱/电话）
- excellent_ratio：优营比例或拟录取人数（如有）

通知标题：{title}

文本：
{content}
"""

EXTENDED_FIELD_LABELS = {
    "requirements": "申请条件",
    "majors": "招收专业",
    "apply_url": "报名链接",
    "materials": "材料清单",
    "contact": "联系方式",
    "excellent_ratio": "优营比例",
}





@dataclass

class LlmExtractResult:

    fields: dict[str, str | None] | None = None

    failure_type: str | None = None

    detail: str = ""

    raw: str = ""

    confidence: float = 0.0

    from_cache: bool = False





def extract_key_paragraphs(content: str, max_chars: int | None = None) -> str:

    limit = max_chars or settings.llm_content_max_chars

    text = re.sub(r"\s+", " ", content.strip())

    if len(text) <= limit:

        return text



    parts = re.split(r"(?<=[。！？；;])\s*|\n{2,}", text)

    scored: list[tuple[int, str]] = []

    for p in parts:

        p = p.strip()

        if len(p) < 6:

            continue

        score = sum(1 for kw in _KEYWORDS if kw in p)

        if score:

            scored.append((score, p))



    scored.sort(key=lambda x: (-x[0], -len(x[1])))

    picked: list[str] = []

    total = 0

    for score, p in scored:

        if total + len(p) > limit:

            remain = limit - total

            if remain > 80:

                picked.append(p[:remain])

            break

        picked.append(p)

        total += len(p)

        if total >= min(1500, limit) and score >= 2:

            break



    if picked:

        return "\n".join(picked)[:limit]

    return text[:limit]





def is_likely_garbled(content: str) -> bool:

    if len(content) < 80:

        return False

    sample = content[:4000]

    cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))

    if cjk / max(len(sample), 1) < 0.08:

        return True

    weird = sample.count("\ufffd") + sample.count("锟") + sample.count("�")

    return weird > 12





def _normalize_datetime(val: str | None) -> str | None:

    if not val or val in ("未知", "null", "None", "无", ""):

        return None

    s = str(val).strip()



    m = re.match(

        r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?",

        s,

    )

    if m:

        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)

        if m.group(4):

            h, mi = m.group(4).zfill(2), m.group(5).zfill(2)

            sec = (m.group(6) or "00").zfill(2)

            return f"{y}-{mo}-{d} {h}:{mi}:{sec}"

        return f"{y}-{mo}-{d} 00:00:00"



    s = s.replace("年", "-").replace("月", "-").replace("日", "").replace("号", "")

    s = re.sub(r"[./]", "-", s)

    s = re.sub(r"\s+", " ", s)

    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)

    if m:

        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)

        if m.group(4):

            h, mi = m.group(4).zfill(2), m.group(5).zfill(2)

            sec = (m.group(6) or "00").zfill(2)

            return f"{y}-{mo}-{d} {h}:{mi}:{sec}"

        return f"{y}-{mo}-{d} 00:00:00"

    return s if len(s) >= 8 else None





def _format_event_time(start: str | None, end: str | None) -> str | None:

    if not start:

        return None

    if not end or start == end:

        return start

    return f"{start} 至 {end}"





def _normalize_format(val: str | None) -> str | None:

    if not val:

        return None

    s = val.strip()

    if s in ("未知", "null", "待定", "详见通知"):

        return None

    if "混合" in s or ("线上" in s and "线下" in s):

        return "线上线下"

    if "线上" in s or "腾讯会议" in s.lower() or "zoom" in s.lower():

        return "线上"

    if "线下" in s:

        return "线下"

    return None





def parse_llm_json(raw: str) -> dict[str, Any]:

    text = raw.strip()

    if text.startswith("```"):

        text = re.sub(r"^```(?:json)?\s*", "", text)

        text = re.sub(r"\s*```$", "", text)

    start = text.find("{")

    end = text.rfind("}")

    if start >= 0 and end > start:

        text = text[start: end + 1]

    return json.loads(text)





def safe_parse_json(text: str) -> dict[str, Any]:

    try:

        return parse_llm_json(text)

    except (json.JSONDecodeError, ValueError):

        fields: dict[str, Any] = {}

        for key in (

            "publish_date", "deadline", "event_start", "event_end",

            "event_format", "format",

        ):

            m = re.search(rf'"{re.escape(key)}"\s*:\s*(?:"([^"]*)"|null)', text)

            if m and m.group(1):

                fields[key] = m.group(1)

        if fields:

            return fields

        raise





def fields_from_llm_payload(data: dict[str, Any]) -> dict[str, str | None]:

    pub = _normalize_datetime(data.get("publish_date"))

    ddl = _normalize_datetime(data.get("deadline"))

    es = data.get("event_start") or data.get("camp_start")

    ee = data.get("event_end") or data.get("camp_end")

    es_n = _normalize_datetime(str(es) if es else None)

    ee_n = _normalize_datetime(str(ee) if ee else None)

    event_time = _format_event_time(es_n, ee_n)

    if event_time and re.match(r"\d{4}-\d{2}-\d{2}", event_time.split(" 至 ")[0]):

        def _cn(dt: str) -> str:

            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", dt)

            if m:

                return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日"

            return dt



        if " 至 " in event_time:

            a, b = event_time.split(" 至 ", 1)

            event_time = f"{_cn(a)}至{_cn(b)}"

        else:

            event_time = _cn(event_time)



    fmt_raw = data.get("event_format") or data.get("format")

    return {

        "publish_date": pub,

        "deadline": ddl,

        "event_time": event_time,

        "event_format": _normalize_format(str(fmt_raw) if fmt_raw else None),

    }





def extract_extended_fields(data: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in EXTENDED_FIELD_LABELS:
        val = data.get(key)
        if val and str(val).strip() not in ("null", "None", "无", ""):
            out[key] = str(val).strip()[:500]
    return out


def append_extended_to_summary(summary: str | None, extended: dict[str, str]) -> str:
    if not extended:
        return summary or ""
    base = (summary or "").split("\n---扩展信息---")[0].strip()
    lines = [f"{EXTENDED_FIELD_LABELS[k]}：{v}" for k, v in extended.items() if v]
    if not lines:
        return base
    block = "\n".join(lines)
    return f"{base}\n---扩展信息---\n{block}".strip() if base else block


def call_llm_extract_all(
    title: str,
    content: str,
    *,
    url: str = "",
    table_text: str = "",
    garbled: bool = False,
) -> LlmExtractResult:
    """一次性抽取四字段 + 扩展字段（requirements/majors/...）。"""
    if not settings.llm_enabled:
        return LlmExtractResult(failure_type="disabled", detail="LLM 未启用")

    if not content or len(content.strip()) < 30:
        if not table_text:
            return LlmExtractResult(failure_type="no_content", detail="正文过短")

    prepared = extract_key_paragraphs(content) if content else table_text[:2000]
    prompt = ENRICH_ALL_PROMPT.format(
        few_shots=format_few_shots_for_prompt(),
        title=(title or "")[:200],
        content=prepared,
    )
    raw, err = call_llm_chat(prompt, temperature=0.1)
    if err:
        ft = "timeout" if err == "timeout" else "bad_json"
        return LlmExtractResult(failure_type=ft, detail=err)
    if not raw:
        return LlmExtractResult(failure_type="bad_json", detail="模型返回空")

    try:
        payload = safe_parse_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        return LlmExtractResult(failure_type="bad_json", detail=str(e), raw=raw[:500])

    fields = fields_from_llm_payload(payload)
    extended = extract_extended_fields(payload)
    merged = dict(fields)
    if extended:
        merged["_extended"] = extended
    conf = compute_field_confidence(fields)
    if not _fields_useful(fields) and not extended:
        return LlmExtractResult(failure_type="empty_fields", detail="JSON 全为 null", raw=raw[:500])
    return LlmExtractResult(fields=merged, failure_type="success", raw=raw[:500], confidence=conf)





def _fields_useful(fields: dict[str, str | None]) -> bool:

    return any(fields.get(k) for k in ("publish_date", "deadline", "event_time", "event_format"))





def _invoke_extract_prompt(prompt: str) -> LlmExtractResult:

    raw, err = call_llm_chat(prompt, temperature=0.1)

    if err:

        ft = "timeout" if err == "timeout" else "bad_json"

        return LlmExtractResult(failure_type=ft, detail=err)

    if not raw:

        return LlmExtractResult(failure_type="bad_json", detail="模型返回空")



    try:

        payload = safe_parse_json(raw)

    except (json.JSONDecodeError, ValueError) as e:

        return LlmExtractResult(failure_type="bad_json", detail=str(e), raw=raw[:500])



    fields = fields_from_llm_payload(payload)

    conf = compute_field_confidence(fields)

    if not _fields_useful(fields):

        return LlmExtractResult(

            failure_type="empty_fields", detail="JSON 全为 null", raw=raw[:500],

        )

    return LlmExtractResult(

        fields=fields, failure_type="success", raw=raw[:200], confidence=conf,

    )





def call_llm_extract(

    title: str,

    content: str,

    *,

    url: str = "",

    table_text: str = "",

    garbled: bool = False,

) -> LlmExtractResult:

    """LLM 主力提取四字段。"""

    if not settings.llm_enabled:

        return LlmExtractResult(failure_type="disabled", detail="LLM 未启用")



    if url and settings.llm_cache_enabled:

        cached = get_cached_extract(url, title)

        if cached and cached.get("fields"):

            return LlmExtractResult(

                fields=cached["fields"],

                failure_type="success",

                confidence=cached.get("confidence", 0.75),

                from_cache=True,

            )



    if not content or len(content.strip()) < 40:

        if not table_text:

            return LlmExtractResult(failure_type="no_content", detail="正文过短")



    if table_text and len(table_text.strip()) >= 30:

        prompt = TABLE_EXTRACT_PROMPT.format(

            title=(title or "")[:200],

            table=table_text[:4000],

        )

        result = _invoke_extract_prompt(prompt)

        if result.failure_type == "success" and result.fields:

            _maybe_cache(url, title, result)

            return result



    garbled_flag = garbled or is_likely_garbled(content)

    if garbled_flag and content:

        prompt = GARBLED_EXTRACT_PROMPT.format(

            title=(title or "")[:200],

            content=content[:2500],

        )

    else:

        prepared = extract_key_paragraphs(content)

        prompt = EXTRACT_PROMPT.format(

            few_shots=format_few_shots_for_prompt(),

            title=(title or "")[:200],

            content=prepared,

        )



    result = _invoke_extract_prompt(prompt)

    if result.failure_type == "success" and result.fields:

        _maybe_cache(url, title, result)

    return result





def _maybe_cache(url: str, title: str, result: LlmExtractResult) -> None:

    if url and settings.llm_cache_enabled and result.fields:

        set_cached_extract(

            url, title, result.fields,

            confidence=result.confidence,

            source="cache_miss",

        )





def merge_llm_then_regex(

    item,

    content: str,

    *,

    url: str = "",

    table_text: str = "",

    html: str | None = None,

) -> LlmExtractResult:

    """

    LLM 优先提取 → 正则仅补 LLM 未填字段（兜底）。

    直接修改 item 上的四字段。

    """

    from crawler.parser import merge_times_into_item



    garbled = is_likely_garbled(content)

    if settings.llm_enrich_all_enabled:
        llm_result = call_llm_extract_all(
            item.title or "", content,
            url=url or getattr(item, "url", ""),
            table_text=table_text,
            garbled=garbled,
        )
    else:
        llm_result = call_llm_extract(
            item.title or "", content,
            url=url or getattr(item, "url", ""),
            table_text=table_text,
            garbled=garbled,
        )



    if llm_result.fields:
        extended = llm_result.fields.pop("_extended", None) if isinstance(llm_result.fields, dict) else None
        for k, v in llm_result.fields.items():
            if v and not getattr(item, k, None):
                setattr(item, k, v)
        if extended and isinstance(extended, dict):
            item.summary = append_extended_to_summary(item.summary, extended)



    if not _all_four(getattr(item, "publish_date", None), item):

        merge_times_into_item(item, content, html)



    if llm_result.fields and _all_four(getattr(item, "publish_date", None), item):

        llm_result.confidence = 1.0

    elif llm_result.fields:

        llm_result.confidence = compute_field_confidence({

            "publish_date": getattr(item, "publish_date", None),

            "deadline": getattr(item, "deadline", None),

            "event_time": getattr(item, "event_time", None),

            "event_format": getattr(item, "event_format", None),

        })



    return llm_result





def _all_four(_pub, item) -> bool:

    return bool(

        getattr(item, "publish_date", None)

        and getattr(item, "deadline", None)

        and getattr(item, "event_time", None)

        and getattr(item, "event_format", None)

    )





def warmup_llm() -> bool:

    if not settings.llm_enabled:

        return False

    sample = (

        "报名开始时间为2026年7月1日上午9时，截止时间为2026年7月15日24:00。"

        "夏令营举办时间为2026年8月5日至2026年8月7日，采用线上形式，腾讯会议号123456789。"

    ) * 2

    r = call_llm_extract("预热", sample)

    ok = r.failure_type == "success"

    if ok:

        logger.info("LLM 预热成功")

    else:

        logger.warning("LLM 预热失败: %s %s", r.failure_type, r.detail)

    return ok

