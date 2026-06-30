"""通知来源标签：入库、API 筛选与前端展示统一口径。"""
from __future__ import annotations

# 对外展示的六大来源（严格分类）
OFFICIAL_LABEL = "学院官网"
WECHAT_LABEL = "微信公众号"
FORUM_LABEL = "保研论坛"
SEARCH_LABEL = "全网检索"
SUBMIT_LABEL = "用户提交"
MANUAL_LABEL = "用户补全"

SOURCE_CATEGORIES: tuple[str, ...] = (
    OFFICIAL_LABEL,
    WECHAT_LABEL,
    FORUM_LABEL,
    SEARCH_LABEL,
    SUBMIT_LABEL,
    MANUAL_LABEL,
)

# 爬虫内部标签 → 学院官网（发现渠道不同，对用户统一为官网通知）
_OFFICIAL_ALIASES = frozenset({
    OFFICIAL_LABEL,
    "学校官网",
    "官方权威",
    "聚焦爬虫",
    "列表探测",
    "研招网",
})

# 保研论坛版块名前缀
_FORUM_PREFIXES = ("保研论坛", "eeban")


def canonical_source(source: str | None) -> str:
    """将任意 source 字段规范为 SOURCE_CATEGORIES 之一。"""
    s = (source or "").strip()
    if not s:
        return OFFICIAL_LABEL
    if MANUAL_LABEL in s:
        return MANUAL_LABEL
    if s == SUBMIT_LABEL or "用户提交" in s:
        return SUBMIT_LABEL
    if s == SEARCH_LABEL:
        return SEARCH_LABEL
    if is_wechat_source(s):
        return WECHAT_LABEL
    if is_forum_source(s):
        return FORUM_LABEL
    if s in _OFFICIAL_ALIASES or s.startswith(OFFICIAL_LABEL):
        return OFFICIAL_LABEL
    return OFFICIAL_LABEL


def normalize_source_for_storage(source: str | None) -> str:
    """入库前规范化来源标签。"""
    return canonical_source(source)


def normalize_source_on_enrich(source: str | None) -> str:
    """LLM 补全后保持来源大类不变，不追加 +LLM 后缀。"""
    return canonical_source(source)


def is_wechat_source(source: str | None) -> bool:
    return (source or "").startswith(WECHAT_LABEL) or "weixin" in (source or "").lower()


def is_forum_source(source: str | None) -> bool:
    s = source or ""
    return any(s.startswith(p) for p in _FORUM_PREFIXES) or s == FORUM_LABEL


def is_official_source(source: str | None) -> bool:
    return canonical_source(source) == OFFICIAL_LABEL


def source_matches_filter(source: str | None, filter_value: str | None) -> bool:
    if not filter_value:
        return True
    return canonical_source(source) == filter_value


def source_display(source: str | None) -> str:
    """前端展示用标签（与 canonical 一致）。"""
    return canonical_source(source)


def source_css_class(source: str | None) -> str:
    """前端 badge 样式类。"""
    mapping = {
        OFFICIAL_LABEL: "source-official",
        WECHAT_LABEL: "source-wechat",
        FORUM_LABEL: "source-forum",
        SEARCH_LABEL: "source-search",
        SUBMIT_LABEL: "source-submit",
        MANUAL_LABEL: "source-manual",
    }
    return mapping.get(canonical_source(source), "source-official")


def count_by_source(sources: list[str | None]) -> dict[str, int]:
    out = {cat: 0 for cat in SOURCE_CATEGORIES}
    for s in sources:
        out[canonical_source(s)] = out.get(canonical_source(s), 0) + 1
    return out
