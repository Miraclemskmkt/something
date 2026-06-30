"""批量 LLM 补全字段不全通知；支持 --classify 仅诊断。"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.field_enricher import (
    FAILURE_LABELS,
    all_fields_complete,
    classify_incomplete_batch,
    enrich_extended_batch,
    enrich_incomplete_batch,
)
from database import SessionLocal, init_db
from models import Announcement

TYPE_HINTS = {
    "timeout": "→ 已截断正文至 3000 字、超时 180s、预热模型",
    "bad_json": "→ 已加强 prompt + JSON 正则兜底",
    "empty_fields": "→ 原文可能无信息，建议人工补全",
    "no_content": "→ 页面抓取失败，检查链接",
    "pdf_garble": "→ 扫描件乱码，建议人工补全",
    "needs_manual": "→ LLM 已失败 2 次，请字段补全 Tab 人工填写",
    "regex_sufficient": "→ 正则可补，运行 enrich 即可写入",
    "llm_ok_pending_apply": "→ LLM 可抽取，运行 enrich 即可",
    "llm_noise": "→ LLM 判定非招生通知，已从库中清除或应删除",
}


def print_classification(buckets: dict[str, list]) -> None:
    total = sum(len(v) for v in buckets.values())
    print(f"\n=== 失败分类（共 {total} 条不完整）===\n")
    order = [
        "needs_manual", "timeout", "bad_json", "empty_fields",
        "pdf_garble", "no_content", "llm_noise", "llm_ok_pending_apply",
        "regex_sufficient", "already_complete", "unknown",
    ]
    seen = set()
    for t in order + list(buckets.keys()):
        if t in seen or t not in buckets:
            continue
        seen.add(t)
        items = buckets[t]
        label = FAILURE_LABELS.get(t, t)
        hint = TYPE_HINTS.get(t, "")
        print(f"【{label}】 {len(items)} 条 {hint}")
        for it in items[:8]:
            extra = f" — {it.get('detail', '')}" if it.get("detail") else ""
            print(f"  id={it['id']} {it.get('title', '')}{extra}")
        if len(items) > 8:
            print(f"  ... 还有 {len(items) - 8} 条")
        print()

    counts = Counter()
    for t, items in buckets.items():
        counts[t] += len(items)
    print("汇总:", dict(counts))


async def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 字段补全 / 失败分类")
    parser.add_argument("--classify", action="store_true", help="仅分类诊断，不写库")
    parser.add_argument("--enrich-all", action="store_true", help="启用扩展字段抽取（10+ 字段）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理条数")
    parser.add_argument("--ids", type=str, default=None, help="指定 id，逗号分隔，如 6,8,9")
    args = parser.parse_args()

    id_list = None
    if args.ids:
        id_list = [int(x.strip()) for x in args.ids.split(",") if x.strip().isdigit()]

    if args.enrich_all:
        from config import settings
        settings.llm_enrich_all_enabled = True
        print("已启用扩展字段抽取 (llm_enrich_all_enabled=true)")

    init_db()
    db = SessionLocal()

    if args.classify:
        buckets = await classify_incomplete_batch(db)
        print_classification(buckets)
        db.close()
        return

    before = db.query(Announcement).count()
    complete_before = sum(1 for a in db.query(Announcement).all() if all_fields_complete(a))
    print(f"补全前: 通知 {before} 条, 四字段齐全 {complete_before}")

    if not (args.enrich_all and id_list):
        processed, done = await enrich_incomplete_batch(db, limit=args.limit, force_llm=True)
        complete_after = sum(1 for a in db.query(Announcement).all() if all_fields_complete(a))
        print(f"处理 {processed} 条, 新达成四字段齐全 {done} 条")
        print(f"补全后: 四字段齐全 {complete_after}/{before}")
    else:
        print("跳过四字段补全，仅扩展字段指定 id")

    if args.enrich_all:
        ext_processed, ext_done = await enrich_extended_batch(db, limit=args.limit, ids=id_list)
        print(f"扩展字段: 处理 {ext_processed} 条, 写入 {ext_done} 条")

    buckets = await classify_incomplete_batch(db)
    print_classification(buckets)
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
