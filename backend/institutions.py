"""院校名录 API 服务层。"""

from crawler.university_config import UNIVERSITY_TARGETS
from institutions_data import REGIONS_ORDER, get_summary, group_by_region
from official_sites import OFFICIAL_COLLEGE_SITES
from site_probe import load_probe_map, load_probe_summary


def _monitored_keys() -> set[tuple[str, str, str]]:
    return {(t.university, t.college, t.college_type) for t in UNIVERSITY_TARGETS}


def _official_maps() -> tuple[dict[tuple[str, str, str], dict], dict[tuple[str, str], dict]]:
    exact: dict[tuple[str, str, str], dict] = {}
    by_type: dict[tuple[str, str], dict] = {}
    for s in OFFICIAL_COLLEGE_SITES:
        info = {"homepage": s.homepage, "note": s.note, "college": s.college}
        exact[(s.university, s.college, s.college_type)] = info
        by_type[(s.university, s.college_type)] = info
    return exact, by_type


def _lookup_official(university, college, college_type, exact, by_type) -> dict:
    return exact.get((university, college, college_type)) or by_type.get((university, college_type), {})


def _probe_for(university, college_type, probe) -> dict:
    for key, val in probe.items():
        if key[0] == university and key[2] == college_type:
            return val
    return {}


def serialize_institution(inst, monitored, exact, by_type, probe) -> dict:
    off = _lookup_official(inst.university, inst.college, inst.college_type, exact, by_type)
    homepage = off.get("homepage", "")
    canon_college = off.get("college") or inst.college
    p = probe.get((inst.university, canon_college, inst.college_type), {})
    if not p and homepage:
        p = _probe_for(inst.university, inst.college_type, probe)
    return {
        "university": inst.university,
        "college": inst.college,
        "college_type": inst.college_type,
        "province": inst.province,
        "region": inst.region,
        "tags": inst.tags,
        "monitored": (inst.university, inst.college, inst.college_type) in monitored
            or (inst.university, canon_college, inst.college_type) in monitored,
        "homepage": homepage,
        "homepage_ok": p.get("homepage_ok") if p else None,
        "notice_ok": p.get("notice_ok") if p else None,
        "note": off.get("note", ""),
    }


def get_institutions(
    college_type: str | None = None,
    region: str | None = None,
    search: str | None = None,
    tag: str | None = None,
) -> dict:
    monitored = _monitored_keys()
    exact, by_type = _official_maps()
    probe = load_probe_map()
    grouped = group_by_region(college_type, region, search, tag)

    regions = []
    for block in grouped:
        provinces = []
        for prov in block["provinces"]:
            provinces.append({
                "province": prov["province"],
                "count": prov["count"],
                "institutions": [
                    serialize_institution(i, monitored, exact, by_type, probe)
                    for i in prov["institutions"]
                ],
            })
        regions.append({
            "region": block["region"],
            "count": block["count"],
            "provinces": provinces,
        })

    summary = get_summary()
    probe_data = load_probe_summary()
    summary = {
        **summary,
        "linked_homepages": len(OFFICIAL_COLLEGE_SITES),
        "homepage_ok": probe_data.get("homepage_ok", 0),
    }
    if college_type or tag:
        summary = {**summary, "filtered_total": sum(r["count"] for r in regions)}

    return {
        "summary": summary,
        "regions": regions,
        "region_list": REGIONS_ORDER,
    }
