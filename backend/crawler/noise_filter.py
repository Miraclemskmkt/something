"""入库噪音过滤：标题/摘要黑白名单 + 专项规则，在正文抓取前拦截噪音。"""

from __future__ import annotations



import json

import logging

import re

from functools import lru_cache

from pathlib import Path

from urllib.parse import urlparse



from crawler.boards import PRE_ADMISSION, SUMMER_CAMP



logger = logging.getLogger(__name__)



BAD_PATTERNS_FILE = Path(__file__).resolve().parent.parent / "data" / "bad_patterns.json"
LEARNED_NOISE_FILE = Path(__file__).resolve().parent.parent / "data" / "learned_noise.json"



# ── 白名单：标题或摘要至少命中一项 ──

TITLE_WHITELIST = (

    "夏令营",

    "开放日",

    "推免",

    "接收推免生",

    "预推免",

    "免试攻读",

    "硕士招生",

    "优秀大学生",

    "暑期营",

)



PRE_ADMISSION_WHITELIST = (

    "预推免",

    "推免",

    "接收推免",

    "接收推免生",

    "免试攻读",

    "硕士招生",

)



# ── 黑名单：命中即丢弃（后置通知、培训、中小学等） ──

TITLE_BLACKLIST = (

    "高中生",

    "中学",

    "青少年",

    "暑期学校",

    "非学历",

    "国际学生",

    "外国留学生",

    "合作办学",

    "研修班",

    "课程班",

    "英语培训班",

    "翻译培训",

    "在职",

    "成人",

    "名单公示",

    "面试成绩",

    "考核结果",

    "录取名单",

    "报到通知",

    "补录",

    "递补",

    "高招咨询",

)



CRAWL_EXCLUDE_KEYWORDS = (

    "名单公示", "入选名单", "面试成绩", "结业", "研修班", "课程班", "录用", "报到", "补录", "递补",

    "营员名单", "考核结果", "综合考核结果", "公示名单", "拟录取名单", "复试名单",

    "暑期学校招生简章", "写作高级研修班", "青年教师",

)



OUTBOUND_CAMP_KEYWORDS = (

    "美国高校夏令营", "美国高校", "外国高校夏令营", "外国高校", "境外高校", "境外大学",

    "发现中国", "未来之桥",

)



COOPERATION_NEWS_KEYWORDS = (

    "共商夏令营合作", "夏令营合作事宜", "再访我院", "来访我院", "一行再访", "访问我院",

    "来访交流", "合作洽谈", "来院访问", "来校访问",

)



INTERNATIONAL_SCHOOL_KEYWORDS = (

    "国际暑期学校", "国际暑期班", "International Summer School", "international summer school",

)



DOMESTIC_CAMP_MARKERS = ("优秀大学生", "推免", "预推免", "接收推免", "保研", "研招")



HIGH_SCHOOL_CAMP_KEYWORDS = (

    "高中生夏令营", "中学生夏令营", "中学夏令营", "高招咨询", "高中生", "中学生",

)



CAMP_RECAP_KEYWORDS = ("圆满完成", "圆满落幕", "成功举办", "顺利举办", "顺利结束", "圆满举行")



FORM_ATTACHMENT_HINTS = (

    "申请表", "推荐表", "专家推荐信", "推荐信", "登记表", "模板下载", "下载表格",

)

NOTICE_DOC_MARKERS = ("招生简章", "招生通知", "报名通知", "工作通知", "实施方案", "活动方案", "活动通知")



RECRUITMENT_INTENT = (

    "招生", "报名", "申请", "接收", "优秀大学生", "推免", "预推免", "招募",

    "招生简章", "招生通知", "报名通知", "校园开放日", "接收推免", "研招", "通知",

)



ACTION_KEYWORDS = ("报名", "申请", "截止", "接收", "欢迎报考")





@lru_cache(maxsize=1)

def _load_learned_noise_raw() -> dict:

    try:

        if LEARNED_NOISE_FILE.is_file():

            return json.loads(LEARNED_NOISE_FILE.read_text(encoding="utf-8"))

    except Exception as e:

        logger.debug("learned_noise load failed: %s", e)

    return {"title_substrings": [], "entries": []}





@lru_cache(maxsize=1)

def _load_bad_patterns() -> dict:

    try:

        if BAD_PATTERNS_FILE.is_file():

            data = json.loads(BAD_PATTERNS_FILE.read_text(encoding="utf-8"))

        else:

            data = {"title_substrings": [], "url_substrings": []}

        learned = _load_learned_noise_raw()

        merged = list(dict.fromkeys(

            data.get("title_substrings", []) + learned.get("title_substrings", [])

        ))

        data["title_substrings"] = merged

        return data

    except Exception as e:

        logger.debug("bad_patterns load failed: %s", e)

    return {"title_substrings": [], "url_substrings": []}





def reload_bad_patterns() -> None:

    _load_bad_patterns.cache_clear()

    _load_learned_noise_raw.cache_clear()





def record_llm_learned_noise(

    title: str,

    content_snippet: str,

    *,

    reason: str = "llm_classifier",

    url: str = "",

) -> None:

    """LLM 判定为 NO 时写入自动学习库。"""

    from datetime import datetime

    title = (title or "").strip()

    snippet = (content_snippet or "").strip()[:300]

    store = dict(_load_learned_noise_raw())

    entries: list = list(store.setdefault("entries", []))

    substrings: list = list(store.setdefault("title_substrings", []))

    sig = f"{title[:40]}|{reason}"

    if any(e.get("sig") == sig for e in entries[-200:]):

        return

    entries.append({

        "sig": sig,

        "title": title[:200],

        "snippet": snippet,

        "reason": reason,

        "url": url[:200],

        "at": datetime.now().isoformat(timespec="seconds"),

    })

    store["entries"] = entries[-300:]

    for candidate in (title[:24], title[:16]):

        if len(candidate) >= 6 and candidate not in substrings:

            substrings.append(candidate)

            break

    for kw in ("高中生", "中学生", "在职", "研修班", "留学生", "高招", "国际暑期"):

        if kw in title or kw in snippet:

            if kw not in substrings:

                substrings.append(kw)

    store["title_substrings"] = substrings[-80:]

    LEARNED_NOISE_FILE.parent.mkdir(parents=True, exist_ok=True)

    LEARNED_NOISE_FILE.write_text(

        json.dumps(store, ensure_ascii=False, indent=2),

        encoding="utf-8",

    )

    reload_bad_patterns()

    logger.info("LLM 噪音已记入学习库: %s", title[:50])





def _blacklist_exception(keyword: str, title: str, combined: str) -> bool:

    """少数黑名单词在含保研/研招语境下可放行。"""

    if keyword == "中学" and "师范" in combined:

        return True

    if keyword in ("暑期学校", "国际学生", "外国留学生"):

        if any(m in combined for m in DOMESTIC_CAMP_MARKERS):

            return True

    if keyword == "中学" and any(m in combined for m in DOMESTIC_CAMP_MARKERS):

        return True

    return False





def is_attachment_or_form(title: str, url: str = "") -> bool:

    """附件、申请表、推荐信等附属文件，不是夏令营招生通知。"""

    t = (title or "").strip()

    if t.startswith("附件") or t.startswith("附件：") or t.startswith("附件:"):

        return True

    path = urlparse(url or "").path.lower()

    if path.endswith((".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar")):

        if not any(m in t for m in NOTICE_DOC_MARKERS):

            return True

    if any(h in t for h in FORM_ATTACHMENT_HINTS):

        if not any(m in t for m in NOTICE_DOC_MARKERS):

            return True

    return False





def is_international_summer_program(title: str, summary: str = "") -> bool:

    t = title or ""

    tl = t.lower()

    if any(k in t for k in INTERNATIONAL_SCHOOL_KEYWORDS):

        if not any(m in t for m in DOMESTIC_CAMP_MARKERS):

            return True

    if "international summer" in tl or ("summer school" in tl and "admissions open" in tl):

        if not any(m in t for m in DOMESTIC_CAMP_MARKERS):

            return True

    if "国际" in t and "暑期学校" in t and not any(m in t for m in DOMESTIC_CAMP_MARKERS):

        return True

    body = summary or ""

    if body and not any(m in body for m in DOMESTIC_CAMP_MARKERS):

        if "non-chinese" in body.lower() or "非中国籍" in body or "留学生" in body:

            return True

    return False





def is_outbound_camp_news(title: str) -> bool:

    t = title or ""

    if any(k in t for k in OUTBOUND_CAMP_KEYWORDS):

        return True

    if any(k in t for k in COOPERATION_NEWS_KEYWORDS):

        return True

    if re.search(r"(美国|英国|法国|德国|日本|韩国|澳洲|澳大利亚).{0,12}(学院|大学).{0,16}(再访|来访|访问|一行)", t):

        return True

    if "夏令营" in t and "合作" in t and not any(k in t for k in RECRUITMENT_INTENT):

        return True

    if "夏令营" in t and "开营" in t and not any(k in t for k in RECRUITMENT_INTENT):

        return True

    return False





def is_high_school_camp_or_recap_news(title: str, summary: str = "") -> bool:

    t = title or ""

    if any(k in t for k in HIGH_SCHOOL_CAMP_KEYWORDS):

        if any(k in t for k in DOMESTIC_CAMP_MARKERS):

            return False

        if "研招" in t:

            return False

        return True

    if "夏令营" in t and any(k in t for k in CAMP_RECAP_KEYWORDS):

        if not any(k in t for k in DOMESTIC_CAMP_MARKERS) and "研招" not in t:

            return True

    return False





def title_college_mismatch(title: str, university: str, college: str) -> bool:

    t = title or ""

    col = college or ""

    if not col or col == "未知":

        return False

    if col in t:

        return False

    short = col.replace("学院", "")

    if len(short) >= 2 and short in t:

        return False

    uni = university or ""

    start = t.find(uni) + len(uni) if uni and uni in t else 0

    snippet = t[start : start + 32]

    m = re.search(r"([\u4e00-\u9fff]{2,14}学院)", snippet)

    if not m:

        return False

    named = m.group(1)

    if named == col or col in named or named in col:

        return False

    if col.endswith("法学院") and "法学" in named:

        return False

    if ("外国语" in col or "外语" in col) and any(k in named for k in ("外语", "外国语", "外文", "英语")):

        return False

    return True





def title_filter(

    title: str,

    summary: str = "",

    *,

    board: str = SUMMER_CAMP,

    phase: str = "notice",

    url: str = "",

) -> tuple[bool, str]:

    """

    入口黑白名单 + 专项规则。

    返回 (是否接受, 拒绝原因)；在正文抓取/深度补全前调用。

    """

    t = title or ""

    combined = f"{t} {summary or ''}".strip()



    patterns = _load_bad_patterns()

    for pat in patterns.get("title_substrings", []):

        if pat and pat in combined:

            return False, f"bad_pattern:{pat}"

    for pat in patterns.get("url_substrings", []):

        if pat and pat in (url or ""):

            return False, f"bad_url:{pat}"



    for kw in TITLE_BLACKLIST:

        if kw in combined and not _blacklist_exception(kw, t, combined):

            return False, f"blacklist:{kw}"



    for kw in CRAWL_EXCLUDE_KEYWORDS:

        if kw in t:

            return False, f"exclude:{kw}"



    if is_high_school_camp_or_recap_news(t, summary):

        return False, "high_school_or_recap"

    if is_attachment_or_form(t, url):

        return False, "attachment_form"

    if is_international_summer_program(t, summary):

        return False, "international_program"

    if is_outbound_camp_news(t):

        return False, "outbound_news"



    if phase == "result":

        return True, ""



    if board == PRE_ADMISSION:

        if not any(k in combined for k in PRE_ADMISSION_WHITELIST):

            return False, "no_whitelist"

        return True, ""



    if not any(k in combined for k in TITLE_WHITELIST):

        return False, "no_whitelist"



    if "夏令营" in t and not any(k in t for k in RECRUITMENT_INTENT):

        if any(k in t for k in ("开营", "举行", "圆满", "合作", "再访", "来访", "访问")):

            return False, "camp_activity_news"



    return True, ""





def passes_title_filter(

    title: str,

    summary: str = "",

    *,

    board: str = SUMMER_CAMP,

    phase: str = "notice",

    url: str = "",

) -> bool:

    ok, _ = title_filter(title, summary, board=board, phase=phase, url=url)

    return ok





def is_noise_title(title: str, url: str = "", summary: str = "") -> bool:

    return not passes_title_filter(title, summary, url=url)





def should_accept_notice(

    title: str,

    *,

    summary: str = "",

    board: str = SUMMER_CAMP,

    phase: str = "notice",

    url: str = "",

) -> bool:

    """入库前最终判定。"""

    if board != SUMMER_CAMP and board != PRE_ADMISSION:

        board = SUMMER_CAMP

    ok, reason = title_filter(title, summary, board=board, phase=phase, url=url)

    if not ok:

        logger.debug("reject notice (%s): %s", reason, (title or "")[:50])

        return False

    if phase == "result":

        return True

    body = summary or ""

    if any(k in (title or "") for k in TITLE_WHITELIST):

        return True

    return any(k in body for k in ACTION_KEYWORDS) and "夏令营" in body

