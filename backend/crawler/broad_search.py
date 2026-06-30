"""泛搜策略：学校全称 + 学院全称 + 关键词（不用 site:）。"""

from config import settings
from crawler.boards import PRE_ADMISSION, SUMMER_CAMP, search_keywords
from crawler.search_exclusions import append_search_exclusions


def build_broad_queries(target, board: str, phase: str) -> list[str]:
    """生成 2 组高命中率泛搜词（不含 site:）。"""
    year = settings.min_notice_year
    keywords = search_keywords(board, phase, target.college_type)
    if not keywords:
        keywords = ["预推免"] if board == PRE_ADMISSION else ["夏令营"]

    uni, col = target.university, target.college
    if board == PRE_ADMISSION:
        templates = [
            f'"{uni}" "{col}" 预推免 {year}',
            f"{uni} {col} 推免 通知 {year}",
        ]
    else:
        templates = [
            f'"{uni}" "{col}" 夏令营 {year}',
            f"{uni} {col} 优秀大学生 夏令营 {year}",
        ]

    queries: list[str] = []
    seen: set[str] = set()
    for q in templates:
        q = append_search_exclusions(q)
        if q not in seen:
            seen.add(q)
            queries.append(q)
    return queries[: settings.broad_search_max_queries]


def pdf_variant_query(base_query: str) -> str:
    """PDF/DOC 并行补充搜索。"""
    return f'{base_query} (filetype:pdf OR filetype:doc OR ext:pdf)'
