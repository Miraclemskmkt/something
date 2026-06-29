"""夏令营 / 预推免 两大板块与检索槽位定义。"""

from models import Announcement

SUMMER_CAMP = "summer_camp"
PRE_ADMISSION = "pre_admission"

BOARD_LABELS = {
    SUMMER_CAMP: "夏令营",
    PRE_ADMISSION: "预推免",
}

SLOT_SUMMER_CAMP_NOTICE = "summer_camp_notice"
SLOT_SUMMER_CAMP_RESULT = "summer_camp_result"
SLOT_PRE_ADMISSION_NOTICE = "pre_admission_notice"
SLOT_PRE_ADMISSION_RESULT = "pre_admission_result"

SUMMER_CAMP_SLOTS = (SLOT_SUMMER_CAMP_NOTICE, SLOT_SUMMER_CAMP_RESULT)
PRE_ADMISSION_SLOTS = (SLOT_PRE_ADMISSION_NOTICE, SLOT_PRE_ADMISSION_RESULT)

BOARD_SLOTS = {
    SUMMER_CAMP: SUMMER_CAMP_SLOTS,
    PRE_ADMISSION: PRE_ADMISSION_SLOTS,
}

EXCELLENT_KEYWORDS = ["优营", "优秀营员", "夏令营优营", "暑期营优营", "考核优秀", "夏令营考核结果"]
PRE_RESULT_KEYWORDS = ["拟录取", "名单", "通过", "考核", "复试", "录取"]


def classify_slot(title: str, event_type: str, status: str) -> str:
    title = title or ""
    is_pre = "预推免" in title or event_type == "预推免"

    if is_pre:
        if any(k in title for k in PRE_RESULT_KEYWORDS) and (
            "名单" in title or "拟录取" in title or "通过" in title or "考核" in title
        ):
            return SLOT_PRE_ADMISSION_RESULT
        return SLOT_PRE_ADMISSION_NOTICE

    if status == "excellent_list" or any(k in title for k in EXCELLENT_KEYWORDS):
        return SLOT_SUMMER_CAMP_RESULT
    return SLOT_SUMMER_CAMP_NOTICE


def slot_board(slot: str) -> str:
    if slot.startswith("pre_admission"):
        return PRE_ADMISSION
    return SUMMER_CAMP


def announcement_board(ann: Announcement) -> str:
    return slot_board(classify_slot(ann.title, ann.event_type, ann.status))


def matches_board(ann: Announcement, board: str) -> bool:
    return announcement_board(ann) == board


def is_relevant_for_crawl(title: str, board: str, phase: str) -> bool:
    title = title or ""
    if "预推免" in title and board == SUMMER_CAMP:
        return False
    if board == SUMMER_CAMP:
        if phase == "notice":
            if any(k in title for k in EXCELLENT_KEYWORDS):
                return False
            return any(k in title for k in ["夏令营", "暑期学校", "开放日", "暑期营", "夏令营招生"])
        return any(k in title for k in EXCELLENT_KEYWORDS) and "预推免" not in title

    if phase == "notice":
        if any(k in title for k in EXCELLENT_KEYWORDS) and "预推免" not in title:
            return False
        return "预推免" in title or ("推免" in title and "夏令营" not in title)
    if "预推免" not in title and "推免" not in title:
        return False
    return any(k in title for k in ["名单", "拟录取", "通过", "考核", "优营", "公示"])


def search_keywords(board: str, phase: str, college_type: str | None = None) -> list[str]:
    """全网检索用词（不含年份，避免搜索引擎过滤；年份由入库规则校验）。"""
    if board == SUMMER_CAMP:
        if phase == "result":
            return ["优营名单", "优秀营员", "夏令营考核"]
        keywords = ["夏令营", "暑期夏令营"]
        if college_type == "law":
            keywords.extend(["非法学 夏令营", "法律硕士 夏令营"])
        return keywords
    if phase == "result":
        return ["预推免名单", "拟录取", "预推免考核"]
    return ["预推免", "推免复试"]


def wechat_keywords(board: str, phase: str) -> list[str]:
    from config import settings

    if board == SUMMER_CAMP:
        keywords = ["2026 优营"] if phase == "result" else ["2026 夏令营", "2026 暑期"]
    else:
        keywords = ["2026 预推免 名单"] if phase == "result" else ["2026 预推免"]
    if settings.wechat_compact_keywords:
        return keywords[:1]
    return keywords
