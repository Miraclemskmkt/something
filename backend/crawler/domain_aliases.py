"""根据学院类型生成常见子域名别名。"""
from __future__ import annotations

from urllib.parse import urlparse

LAW_PREFIX_ALIASES = ("fxy", "lf", "lawschool", "wenfa", "zfxy", "faxue", "lawschool")
FOREIGN_PREFIX_ALIASES = (
    "wyxy", "flc", "fld", "wwxy", "wgy", "sfs", "foreign", "wy", "sis", "fl", "sfl",
    "waiyu", "cfl", "dfll",
)


def _suffix(host: str) -> str:
    host = host.lower().replace("www.", "")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("edu", "ac") and parts[-1] == "cn":
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def alias_hosts(homepage: str, college_type: str, *, limit: int = 6) -> list[str]:
    """从当前域名生成 2–6 个候选子域（不含当前前缀）。"""
    if not homepage or not homepage.startswith("http"):
        return []
    host = urlparse(homepage).netloc.lower().replace("www.", "")
    parts = host.split(".")
    if len(parts) < 2:
        return []
    prefix = parts[0]
    suffix = _suffix(host)
    pool = LAW_PREFIX_ALIASES if college_type == "law" else FOREIGN_PREFIX_ALIASES
    out: list[str] = []
    seen: set[str] = {host}
    for alt in pool:
        if alt == prefix:
            continue
        candidate = f"{alt}.{suffix}"
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
        if len(out) >= limit:
            break
    return out
