"""搜索引擎减号排除词：在泛搜/site 检索阶段减少噪音进量。"""

# Bing / Google / Baidu 均支持 `-关键词`（半角减号，前有空格）
SEARCH_EXCLUDE_TERMS = (
    "-高中生",
    "-中学生",
    "-青少年",
    "-国际",
    "-研修班",
    "-课程班",
    "-非学历",
    "-在职",
    "-成人",
    "-留学生",
    "-高招咨询",
)


def append_search_exclusions(query: str) -> str:
    """在检索词末尾追加减号排除词。"""
    q = (query or "").strip()
    if not q:
        return q
    suffix = " ".join(SEARCH_EXCLUDE_TERMS)
    if suffix in q:
        return q
    return f"{q} {suffix}"
