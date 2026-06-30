"""生成 data/baoyan_sites.json：学科版块 + 双一流院校专属版块。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from double_first_class import DOUBLE_FIRST_CLASS_UNIVERSITIES

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "baoyan_sites.json"
EE_BAN = "https://www.eeban.com/forum.php"

KEYWORD_FILTERS = [
    "法学院", "法学", "法律", "法硕", "国际法",
    "外国语", "外语", "外文", "英语", "翻译", "语言", "高翻",
]

# 学科分类版块（不限学校名）
SUBJECT_BOARDS = [
    (615, "保研论坛-经管法学", "subject"),
    (640, "保研论坛-英语专版", "subject"),
]

# 优先监控的院校（C9 + 政法财经外语强校 + 用户常关注）
PRIORITY_UNIS = [
    "清华大学", "北京大学", "中国人民大学", "复旦大学", "上海交通大学",
    "浙江大学", "南京大学", "武汉大学", "华中科技大学", "中山大学",
    "厦门大学", "南开大学", "天津大学", "北京师范大学", "同济大学",
    "东南大学", "西安交通大学", "四川大学", "山东大学", "吉林大学",
    "哈尔滨工业大学", "大连理工大学", "北京航空航天大学", "华东师范大学",
    "中国政法大学", "对外经济贸易大学", "中南财经政法大学", "北京外国语大学",
    "上海外国语大学", "暨南大学", "湖南大学", "重庆大学", "兰州大学",
    "苏州大学", "郑州大学", "云南大学",
]


def fetch_forum_map() -> dict[str, int]:
    r = httpx.get(EE_BAN, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out: dict[str, int] = {}
    for m in re.finditer(r"forum-(\d+)-1\.html[^>]*>([^<]+)</a>", r.text):
        fid, name = int(m.group(1)), m.group(2).strip()
        if name and name not in out:
            out[name] = fid
    return out


def main() -> None:
    forum_map = fetch_forum_map()
    sources: list[dict] = []

    for fid, name, mode in SUBJECT_BOARDS:
        sources.append({
            "name": name,
            "type": "eeban_forum",
            "forum_id": fid,
            "mode": mode,
            "pages": 3,
            "keyword_filters": KEYWORD_FILTERS,
            "enabled": True,
        })

    all_unis = {u.name for u in DOUBLE_FIRST_CLASS_UNIVERSITIES}
    targets = [u for u in PRIORITY_UNIS if u in all_unis]
    missing = [u for u in PRIORITY_UNIS if u not in forum_map]
    if missing:
        print("未找到版块:", ", ".join(missing[:10]))

    for uni in targets:
        fid = forum_map.get(uni)
        if not fid:
            continue
        sources.append({
            "name": f"保研论坛-{uni}",
            "type": "eeban_forum",
            "forum_id": fid,
            "mode": "university",
            "university": uni,
            "pages": 3,
            "keyword_filters": KEYWORD_FILTERS,
            "enabled": True,
        })

    payload = {"sources": sources}
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写入 {len(sources)} 个版块 → {OUTPUT}")


if __name__ == "__main__":
    main()
