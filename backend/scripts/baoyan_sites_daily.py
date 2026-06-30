"""保研类网站聚合抓取（保研论坛等）。"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.wechat_jobs import run_baoyan_sites_crawl, run_wechat_daily


def main() -> None:
    p = argparse.ArgumentParser(description="保研网站聚合抓取")
    p.add_argument("--all", action="store_true", help="跑全部聚合通道（含 RSS）")
    args = p.parse_args()

    if args.all:
        result = asyncio.run(run_wechat_daily())
    else:
        found, new = asyncio.run(run_baoyan_sites_crawl())
        result = {"baoyan_found": found, "baoyan_new": new}

    print("完成:", result)


if __name__ == "__main__":
    main()
