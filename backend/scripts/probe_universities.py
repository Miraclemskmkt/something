"""批量探测147所双一流高校官网及学院通知页可达性。"""
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.college_discovery import probe_all
from double_first_class import DOUBLE_FIRST_CLASS_UNIVERSITIES

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "probe_report.json"


async def main():
    print(f"开始探测 {len(DOUBLE_FIRST_CLASS_UNIVERSITIES)} 所高校...")
    results = await probe_all(DOUBLE_FIRST_CLASS_UNIVERSITIES)

    report = {
        "total_universities": len(DOUBLE_FIRST_CLASS_UNIVERSITIES),
        "probes": len(results),
        "main_ok": sum(1 for r in results if r.main_ok),
        "college_found": sum(1 for r in results if r.college_found),
        "notice_ok": sum(1 for r in results if r.notice_ok),
        "details": [
            {
                "university": r.university,
                "college_type": r.college_type,
                "main_url": r.main_url,
                "main_ok": r.main_ok,
                "main_status": r.main_status,
                "college_found": r.college_found,
                "notice_ok": r.notice_ok,
                "news_urls": r.news_urls,
                "error": r.error,
            }
            for r in results
        ],
    }

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    main_fail = [r for r in results if not r.main_ok]
    notice_fail = [r for r in results if r.main_ok and not r.notice_ok]

    print(f"\n=== 探测结果 ===")
    print(f"官网可达: {report['main_ok']}/{len(results)} (按学院类型计)")
    print(f"找到学院: {report['college_found']}/{len(results)}")
    print(f"通知页可用: {report['notice_ok']}/{len(results)}")
    print(f"\n官网不可达 ({len(main_fail)}):")
    for r in main_fail[:20]:
        print(f"  - {r.university} {r.main_url} [{r.main_status}] {r.error}")
    print(f"\n官网可达但通知页未定位 ({len(notice_fail)}):")
    for r in notice_fail[:20]:
        print(f"  - {r.university} ({r.college_type}) {r.error}")

    by_uni_main = Counter(r.university for r in results if r.main_ok)
    print(f"\n至少一类学院官网可达的高校: {len(by_uni_main)}/{len(DOUBLE_FIRST_CLASS_UNIVERSITIES)}")
    print(f"报告已保存: {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
