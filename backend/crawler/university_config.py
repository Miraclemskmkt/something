"""Target universities with law / foreign language colleges and their news pages."""

from dataclasses import dataclass

from college_registry import REGISTRY_ENTRIES


@dataclass
class UniversityTarget:
    university: str
    college: str
    college_type: str  # "law" | "foreign_lang"
    news_urls: list[str]
    base_url: str = ""
    wechat_name: str = ""  # 微信公众号名称，留空则自动推断


def get_wechat_account(target: UniversityTarget) -> str:
    if target.wechat_name:
        return target.wechat_name
    key = (target.university, target.college)
    if key in WECHAT_NAME_OVERRIDES:
        return WECHAT_NAME_OVERRIDES[key]
    return f"{target.university}{target.college}"


def _to_target(entry) -> UniversityTarget:
    return UniversityTarget(
        university=entry.university,
        college=entry.college,
        college_type=entry.college_type,
        news_urls=entry.news_urls,
        base_url=entry.base_url,
    )


UNIVERSITY_TARGETS: list[UniversityTarget] = [_to_target(e) for e in REGISTRY_ENTRIES]

# 微信公众号名称与学院全称不一致时的映射
WECHAT_NAME_OVERRIDES: dict[tuple[str, str], str] = {
    ("北京大学", "法学院"): "北大法学",
    ("清华大学", "法学院"): "清华法学",
    ("中国人民大学", "法学院"): "人大法学",
    ("武汉大学", "法学院"): "武大法学",
    ("复旦大学", "法学院"): "复旦法学",
    ("中国政法大学", "法学院"): "中国政法大学法学院",
    ("北京外国语大学", "外国语学院"): "北外研招",
    ("上海外国语大学", "外国语学院"): "上外研招",
    ("对外经济贸易大学", "法学院"): "贸大法学",
    ("对外经济贸易大学", "外国语学院"): "贸大外语",
    ("华东师范大学", "外国语学院"): "华东师大外院",
}

WECHAT_SEARCH_KEYWORDS = ["2026 夏令营", "2026 预推免", "2026 优营", "2027 夏令营", "2027 预推免"]

LAW_KEYWORDS = ["法", "法律", "法学", "国际法", "司法", "政"]
FOREIGN_LANG_KEYWORDS = [
    "外国语", "外语", "英语", "翻译", "文学", "法语", "德语",
    "日语", "俄语", "西班牙语", "朝鲜语", "阿拉伯语", "语言",
]
CAMP_KEYWORDS = ["夏令营", "预推免", "推免", "暑期学校", "开放日"]
EXCELLENT_KEYWORDS = ["优营", "优秀营员", "夏令营优营", "暑期营优营", "考核优秀", "夏令营考核结果"]
ENDED_KEYWORDS = ["已结束", "报名截止", "活动结束", "公示完毕"]
