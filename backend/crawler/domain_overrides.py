"""学院官网域名修正持久化（HEAD 探测 / 主站反查结果）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_OVERRIDE_FILE = Path(__file__).resolve().parent.parent / "data" / "domain_overrides.json"

COLLEGE_HOST_MARKERS = (
    "fxy", "law", "sfl", "wyxy", "flc", "fld", "wfxy", "foreign", "wgy", "sis", "fl",
    "sfs", "wwxy", "wy", "gjfx", "deis", "yyxy", "renwen", "hc", "cfl", "wgyxy", "sl",
    "rwy", "faxue", "wenfa", "zfxy", "fls", "sflc", "waiyuan", "wgy", "gjxy", "fsc",
    "sden", "shl", "fc", "sis", "fld", "dfll", "wywy", "wgyxy", "rw", "xrw", "wf",
)

ROOT_HOST_BLOCKLIST = (
    "www", "news", "english", "bulletin", "zyd", "yz", "yjszs", "graduate", "yzb",
    "gs", "zsb", "admission", "postgraduate", "gra", "gsao", "pgs", "international",
)


def _load_raw() -> dict[str, str]:
    if not _OVERRIDE_FILE.exists():
        return {}
    try:
        return json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取 domain_overrides 失败: %s", e)
        return {}


def _save_raw(data: dict[str, str]) -> None:
    _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _key(university: str, college: str, college_type: str) -> str:
    return f"{university}|{college}|{college_type}"


def _parse_key(key: str) -> tuple[str, str, str]:
    parts = key.split("|", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return key, "", ""


def is_valid_college_homepage(url: str, university: str) -> tuple[bool, str]:
    """禁止映射到学校根域、新闻首页、招生办门户。"""
    from crawler.official_verify import UNIVERSITY_ROOT_DOMAINS

    if not url or not url.startswith("http"):
        return False, "非 HTTP URL"
    host = urlparse(url).netloc.lower().replace("www.", "")
    root = UNIVERSITY_ROOT_DOMAINS.get(university, "").lower().replace("www.", "")
    if not host:
        return False, "无 host"
    if root and (host == root):
        return False, "学校根域"
    prefix = host.split(".")[0]
    if prefix in ROOT_HOST_BLOCKLIST:
        return False, f"禁止前缀 {prefix}"
    if host.startswith("news.") and root and root in host:
        return False, "新闻首页"
    if any(m in prefix for m in COLLEGE_HOST_MARKERS):
        return True, "ok"
    if root and host.endswith(root) and len(prefix) >= 3:
        return True, "subdomain"
    return False, "无学院标识子域"


def get_homepage_override(university: str, college: str, college_type: str) -> str | None:
    url = _load_raw().get(_key(university, college, college_type))
    if not url:
        return None
    ok, _ = is_valid_college_homepage(url, university)
    return url if ok else None


def set_homepage_override(
    university: str,
    college: str,
    college_type: str,
    homepage: str,
    *,
    source: str = "",
) -> bool:
    homepage = homepage.rstrip("/")
    ok, reason = is_valid_college_homepage(homepage, university)
    if not ok:
        logger.warning(
            "拒绝域名覆盖 %s %s → %s (%s): %s",
            university, college, homepage, source, reason,
        )
        return False
    data = _load_raw()
    data[_key(university, college, college_type)] = homepage
    _save_raw(data)
    logger.info("域名覆盖 %s %s → %s (%s)", university, college, homepage, source)
    return True


def remove_override(university: str, college: str, college_type: str) -> bool:
    data = _load_raw()
    k = _key(university, college, college_type)
    if k not in data:
        return False
    del data[k]
    _save_raw(data)
    return True


def clean_invalid_overrides() -> list[tuple[str, str, str, str, str]]:
    """删除无效条目，返回 [(key, url, reason), ...]。"""
    data = _load_raw()
    removed: list[tuple[str, str, str, str, str]] = []
    kept: dict[str, str] = {}
    for key, url in data.items():
        uni, col, ctype = _parse_key(key)
        ok, reason = is_valid_college_homepage(url, uni)
        if ok:
            kept[key] = url
        else:
            removed.append((uni, col, ctype, url, reason))
    if len(kept) != len(data):
        _save_raw(kept)
    return removed


def all_overrides() -> dict[str, str]:
    return {
        k: v for k, v in _load_raw().items()
        if is_valid_college_homepage(v, _parse_key(k)[0])[0]
    }
