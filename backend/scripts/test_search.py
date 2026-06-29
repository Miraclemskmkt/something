"""快速测试全网检索 + 官方校验（默认只测 10 个学院，约 30 秒）。

用法:
  python scripts/test_search.py
  python scripts/test_search.py --limit 5
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.searcher import search_for_targets
from crawler.university_config import UNIVERSITY_TARGETS


def parse_args():
    p = argparse.ArgumentParser(description="快速测试全网检索")
    p.add_argument("--limit", type=int, default=10, help="测试学院数量（默认 10）")
    p.add_argument("--board", default="summer_camp", choices=["summer_camp", "pre_admission"])
    p.add_argument("--phase", default="notice", choices=["notice", "result"])
    return p.parse_args()


async def main():
    args = parse_args()
    targets = UNIVERSITY_TARGETS[: args.limit]
    print(f"测试 {len(targets)} 个学院...")
    items = await search_for_targets(targets, args.board, args.phase)
    print(f"命中 {len(items)} 条")
    for item in items[:10]:
        print(f"  [{item.source}] {item.university} | {item.title[:70]}")
        print(f"    {item.url}")


if __name__ == "__main__":
    asyncio.run(main())
