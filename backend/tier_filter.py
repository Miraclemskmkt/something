"""985 / 211 / 双一流 互斥分层（同一学校只归入最高档）。"""

from institutions_data import INSTITUTIONS

VALID_TIERS = ("985", "211", "双一流")


def resolve_tier(tags: list[str]) -> str | None:
    if "985" in tags:
        return "985"
    if "211" in tags:
        return "211"
    if "双一流" in tags:
        return "双一流"
    return None


def build_university_tier_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for inst in INSTITUTIONS:
        tier = resolve_tier(inst.tags)
        if tier:
            mapping[inst.university] = tier
    return mapping


UNIVERSITY_TIER: dict[str, str] = build_university_tier_map()


def universities_in_tier(tier: str) -> set[str]:
    if tier not in VALID_TIERS:
        return set()
    return {u for u, t in UNIVERSITY_TIER.items() if t == tier}


def university_in_tier(university: str, tier: str) -> bool:
    return UNIVERSITY_TIER.get(university) == tier


def filter_targets_by_tier(targets: list, tier: str | None) -> list:
    if not tier or tier == "all":
        return list(targets)
    allowed = universities_in_tier(tier)
    return [t for t in targets if t.university in allowed]


def filter_items_by_tier(items: list, tier: str) -> list:
    return [item for item in items if university_in_tier(getattr(item, "university", ""), tier)]


def tier_label(tier: str) -> str:
    return {"985": "985", "211": "211", "双一流": "双一流"}.get(tier, tier)
