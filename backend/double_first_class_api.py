"""双一流147所高校名录 API（含学院官网与探测状态）。"""

from college_registry import REGISTRY_ENTRIES
from double_first_class import DOUBLE_FIRST_CLASS_UNIVERSITIES, PROVINCE_TO_REGION
from institutions_data import REGIONS_ORDER
from crawler.university_config import UNIVERSITY_TARGETS
from official_sites import OFFICIAL_COLLEGE_SITES
from site_probe import load_probe_map, load_probe_summary


def _monitored_keys() -> set[tuple[str, str, str]]:
    return {(t.university, t.college, t.college_type) for t in UNIVERSITY_TARGETS}


def _site_map() -> dict[tuple[str, str, str], dict]:
    probe = load_probe_map()
    m = {}
    for s in OFFICIAL_COLLEGE_SITES:
        key = (s.university, s.college, s.college_type)
        p = probe.get(key, {})
        m[key] = {
            "homepage": s.homepage,
            "note": s.note,
            "homepage_ok": p.get("homepage_ok"),
            "notice_ok": p.get("notice_ok"),
            "official_domain": p.get("official_domain", True),
        }
    return m


def get_double_first_class(
    college_type: str | None = None,
    region: str | None = None,
    search: str | None = None,
    tag: str | None = None,
) -> dict:
    monitored = _monitored_keys()
    sites = _site_map()
    registry_by_uni: dict[str, list] = {}
    for e in REGISTRY_ENTRIES:
        registry_by_uni.setdefault(e.university, []).append(e)

    probe_data = load_probe_summary()
    regions: dict[str, dict] = {}
    stats = {
        "total": len(DOUBLE_FIRST_CLASS_UNIVERSITIES),
        "college_sites": len(OFFICIAL_COLLEGE_SITES),
        "homepage_ok": probe_data.get("homepage_ok", 0),
        "notice_ok": probe_data.get("notice_ok", 0),
        "monitored_colleges": len(REGISTRY_ENTRIES),
        "tag_985": sum(1 for u in DOUBLE_FIRST_CLASS_UNIVERSITIES if "985" in u.tags),
        "tag_211": sum(1 for u in DOUBLE_FIRST_CLASS_UNIVERSITIES if "211" in u.tags),
        "tag_dfc": len(DOUBLE_FIRST_CLASS_UNIVERSITIES),
    }

    for uni in DOUBLE_FIRST_CLASS_UNIVERSITIES:
        if search and search not in uni.name and search not in uni.province:
            continue
        r = PROVINCE_TO_REGION.get(uni.province, "其他")
        if region and r != region:
            continue
        if tag and tag not in uni.tags:
            continue

        colleges = registry_by_uni.get(uni.name, [])
        if college_type:
            colleges = [c for c in colleges if c.college_type == college_type]
        if college_type and not colleges:
            continue

        college_items = []
        for c in colleges:
            key = (c.university, c.college, c.college_type)
            info = sites.get(key, {})
            college_items.append({
                "college": c.college,
                "college_type": c.college_type,
                "homepage": info.get("homepage") or c.base_url,
                "news_urls": c.news_urls,
                "url_source": c.source,
                "monitored": key in monitored,
                "homepage_ok": info.get("homepage_ok"),
                "notice_ok": info.get("notice_ok"),
                "note": info.get("note", ""),
            })

        item = {
            "name": uni.name,
            "url": uni.url,
            "province": uni.province,
            "region": r,
            "tags": uni.tags,
            "colleges": college_items,
            "has_law": any(c.college_type == "law" for c in registry_by_uni.get(uni.name, [])),
            "has_foreign": any(
                c.college_type == "foreign_lang" for c in registry_by_uni.get(uni.name, [])
            ),
        }

        if r not in regions:
            regions[r] = {"region": r, "provinces": {}, "count": 0}
        prov = uni.province
        if prov not in regions[r]["provinces"]:
            regions[r]["provinces"][prov] = []
        regions[r]["provinces"][prov].append(item)
        regions[r]["count"] += 1

    region_list = []
    for r in REGIONS_ORDER:
        if r not in regions:
            continue
        provinces = []
        for prov in sorted(regions[r]["provinces"].keys()):
            unis = sorted(regions[r]["provinces"][prov], key=lambda x: x["name"])
            provinces.append({"province": prov, "count": len(unis), "universities": unis})
        region_list.append({"region": r, "count": regions[r]["count"], "provinces": provinces})

    if tag or college_type or region or search:
        stats["filtered_total"] = sum(r["count"] for r in region_list)

    return {"summary": stats, "regions": region_list, "region_list": REGIONS_ORDER}
