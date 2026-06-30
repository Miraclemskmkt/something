"""LLM 自动发现保研论坛相关版块，合并写入 baoyan_sites.json。"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from crawler.llm_classifier import _parse_yes_no
from crawler.llm_client import call_llm_chat

EE_BAN = "https://www.eeban.com"
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "baoyan_sites.json"

# 与 build_baoyan_forums 一致：院校版仅自动纳入优先校，避免一次膨胀到 100+
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

SKIP_NAME = (
    "竞赛", "灌水", "闲聊", "交友", "二手", "游戏", "娱乐", "情感", "职场",
    "考公", "留学", "求职", "租房", "版务", "公告", "站务", "测试",
    "资源", "下载", "软件", "电影", "音乐", "体育", "美食", "旅游", "宠物",
    "经验交流", "经验专版", "个人show", "热门专业", "ee读书",
)
KEYWORD_FILTERS = ["法学院", "外国语", "外院", "英语", "翻译", "法学"]

BOARD_CLASSIFY_PROMPT = """判断以下论坛版块是否可能包含「国内高校法学院或外国语/外语学院面向本科生的保研夏令营或预推免招生通知」的帖子搬运或讨论。
以下类型必须回答 NO：竞赛专区、灌水闲聊、交友、二手、游戏、纯经验问答、考研非保研。
仅回复 YES 或 NO，不要解释。

版块名称：{name}
答案："""


def fetch_all_forums() -> list[tuple[int, str]]:
    """从 forum.php 及首页提取 forum_id → 名称。"""
    headers = {"User-Agent": settings.user_agent or "Mozilla/5.0"}
    urls = [
        f"{EE_BAN}/forum.php",
        f"{EE_BAN}/forum.php?gid=1",
        EE_BAN,
    ]
    found: dict[int, str] = {}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        for url in urls:
            try:
                r = client.get(url)
                if r.status_code != 200:
                    continue
                for m in re.finditer(r"forum-(\d+)-1\.html[^>]*>([^<]+)</a>", r.text):
                    fid, name = int(m.group(1)), m.group(2).strip()
                    name = re.sub(r"\s+", "", name)
                    if name and fid not in found:
                        found[fid] = name
            except Exception as e:
                print(f"抓取失败 {url}: {e}")
    return sorted(found.items(), key=lambda x: x[1])


def rule_prefilter(name: str) -> bool:
    if not name or len(name) < 2:
        return False
    if any(k in name for k in SKIP_NAME):
        return False
    return True


def auto_include(name: str) -> bool:
    """优先院校专属版 + 法学/外语/经管学科版 → 无需 LLM。"""
    clean = name.strip()
    if clean in PRIORITY_UNIS:
        return True
    subject_hints = ("法学", "法律", "外语", "外国语", "英语", "翻译", "语言", "经管")
    return any(h in name for h in subject_hints)


def llm_board_relevant(name: str) -> bool:
    if not settings.llm_enabled:
        hints = ("法学", "法律", "外语", "外国语", "英语", "翻译", "语言", "人文", "文科", "经管")
        return any(h in name for h in hints)
    prompt = BOARD_CLASSIFY_PROMPT.format(name=name[:120])
    raw, err = call_llm_chat(
        prompt,
        model=settings.llm_classify_model,
        timeout=settings.llm_classify_timeout_sec,
        temperature=0.0,
    )
    if err:
        print(f"  LLM 失败 {name}: {err}，规则兜底")
        return any(k in name for k in ("法学", "外语", "英语", "翻译", "人文", "文科"))
    verdict = _parse_yes_no(raw or "")
    return verdict is True


def should_include(name: str) -> bool:
    if not rule_prefilter(name):
        return False
    if auto_include(name):
        return True
    return llm_board_relevant(name)


def infer_mode(name: str) -> tuple[str, str | None]:
    """返回 (mode, university)。"""
    from double_first_class import DOUBLE_FIRST_CLASS_UNIVERSITIES

    unis = {u.name for u in DOUBLE_FIRST_CLASS_UNIVERSITIES}
    for uni in sorted(unis, key=len, reverse=True):
        if uni in name or name == uni:
            return "university", uni
    return "subject", None


def merge_sources(discovered: list[dict], *, dry_run: bool = False) -> int:
    existing: list[dict] = []
    if OUTPUT.is_file():
        try:
            existing = json.loads(OUTPUT.read_text(encoding="utf-8")).get("sources", [])
        except Exception:
            existing = []

    by_fid = {int(s["forum_id"]): s for s in existing if s.get("forum_id")}
    added = 0
    for src in discovered:
        fid = int(src["forum_id"])
        if fid in by_fid:
            continue
        by_fid[fid] = src
        added += 1
        print(f"  + [{fid}] {src['name']} ({src['mode']})")

    if dry_run:
        print(f"（dry-run）将新增 {added} 个版块")
        return added

    merged = list(by_fid.values())
    merged.sort(key=lambda s: (0 if s.get("mode") == "subject" else 1, s.get("name", "")))
    OUTPUT.write_text(
        json.dumps({"sources": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"写入 {len(merged)} 个版块（新增 {added}）→ {OUTPUT}")
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 发现保研论坛相关版块")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写文件")
    parser.add_argument("--limit", type=int, default=None, help="最多 LLM 判断条数")
    args = parser.parse_args()

    print("抓取版块列表…")
    forums = fetch_all_forums()
    print(f"共 {len(forums)} 个版块")

    candidates = [(fid, name) for fid, name in forums if rule_prefilter(name)]
    print(f"规则预筛后 {len(candidates)} 个")

    if args.limit:
        candidates = candidates[: args.limit]

    discovered: list[dict] = []
    for i, (fid, name) in enumerate(candidates, 1):
        via = "规则" if auto_include(name) else "LLM"
        print(f"[{i}/{len(candidates)}] {name} …", end=" ", flush=True)
        if not should_include(name):
            print("NO")
            continue
        print(f"YES ({via})")
        mode, uni = infer_mode(name)
        entry: dict = {
            "name": f"保研论坛-{name}" if not name.startswith("保研") else name,
            "type": "eeban_forum",
            "forum_id": fid,
            "mode": mode,
            "pages": 3,
            "keyword_filters": KEYWORD_FILTERS,
            "enabled": True,
        }
        if uni:
            entry["university"] = uni
        discovered.append(entry)
        time.sleep(0.3)

    print(f"\nLLM 判定相关: {len(discovered)} 个")
    merge_sources(discovered, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
