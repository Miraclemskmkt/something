"""批量验证权威学院子站可达性及通知页路径。"""

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from college_registry import REGISTRY_ENTRIES
from official_sites import OFFICIAL_COLLEGE_SITES, derive_news_urls

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "official_sites_probe.json"


def is_official_domain(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith(".edu.cn") or host.endswith(".ac.cn")


async def check_url(client: httpx.AsyncClient, url: str) -> tuple[bool, int, str]:
    urls = [url]
    if url.startswith("https://"):
        urls.append("http://" + url[8:])
    last_err = ""
    for u in urls:
        try:
            resp = await client.get(u, follow_redirects=True)
            ok = resp.status_code < 400
            return ok, resp.status_code, ""
        except Exception as e:
            last_err = str(e)
    return False, -1, last_err


async def probe_one(client: httpx.AsyncClient, site) -> dict:
    homepage = site.homepage
    official = is_official_domain(homepage)
    hp_ok, hp_status, hp_err = await check_url(client, homepage)

    notice_ok = False
    notice_url = ""
    notice_status = 0
    candidates = []
    key = (site.university, site.college_type)
    for entry in REGISTRY_ENTRIES:
        if entry.university == site.university and entry.college_type == site.college_type:
            candidates = entry.news_urls
            break
    if not candidates:
        candidates = derive_news_urls(homepage)

    for url in candidates[:6]:
        ok, status, _ = await check_url(client, url)
        if ok:
            notice_ok = True
            notice_url = url
            notice_status = status
            break

    return {
        "university": site.university,
        "college": site.college,
        "college_type": site.college_type,
        "homepage": homepage,
        "official_domain": official,
        "homepage_ok": hp_ok,
        "homepage_status": hp_status,
        "homepage_error": hp_err,
        "notice_ok": notice_ok,
        "notice_url": notice_url,
        "notice_status": notice_status,
    }


async def main():
    print(f"验证 {len(OFFICIAL_COLLEGE_SITES)} 个学院子站...")
    timeout = httpx.Timeout(15.0, connect=10.0)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CampCrawler/1.0)"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, verify=False) as client:
        sem = asyncio.Semaphore(8)

        async def run(site):
            async with sem:
                return await probe_one(client, site)

        results = await asyncio.gather(*[run(s) for s in OFFICIAL_COLLEGE_SITES])

    hp_ok = sum(1 for r in results if r["homepage_ok"])
    notice_ok = sum(1 for r in results if r["notice_ok"])
    official = sum(1 for r in results if r["official_domain"])

    report = {
        "total": len(results),
        "official_domain": official,
        "homepage_ok": hp_ok,
        "notice_ok": notice_ok,
        "details": results,
    }
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 验证结果 ===")
    print(f"权威 edu.cn 域名: {official}/{len(results)}")
    print(f"学院首页可达: {hp_ok}/{len(results)}")
    print(f"通知页可达: {notice_ok}/{len(results)}")
    print(f"报告: {OUTPUT}")

    fail = [r for r in results if not r["homepage_ok"]]
    if fail:
        print(f"\n首页不可达 ({len(fail)}):")
        for r in fail[:15]:
            print(f"  - {r['university']} {r['college']} {r['homepage']} [{r['homepage_status']}]")


if __name__ == "__main__":
    asyncio.run(main())
