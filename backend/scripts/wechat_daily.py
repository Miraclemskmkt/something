"""聚合通道日常任务：保研网站 + RSS（搜狗默认关闭）。"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.wechat_jobs import run_baoyan_sites_crawl, run_wechat_daily, run_wechat_rss_crawl


def main() -> None:
    p = argparse.ArgumentParser(description="聚合通道：保研网站 + RSS")
    p.add_argument("--sogou", action="store_true", help="强制搜狗（不推荐）")
    p.add_argument("--baoyan-only", action="store_true", help="仅跑保研网站")
    p.add_argument("--rss-only", action="store_true", help="仅跑 RSS")
    args = p.parse_args()

    if args.sogou:
        result = asyncio.run(run_wechat_daily(force_sogou=True))
    elif args.baoyan_only:
        found, new = asyncio.run(run_baoyan_sites_crawl())
        result = {"baoyan_found": found, "baoyan_new": new}
    elif args.rss_only:
        found, new = asyncio.run(run_wechat_rss_crawl())
        result = {"rss_found": found, "rss_new": new}
    else:
        result = asyncio.run(run_wechat_daily())

    print("聚合通道完成:", result)


if __name__ == "__main__":
    main()
