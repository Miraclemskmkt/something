"""论坛雷达命中后，用真实通知 URL 反推学院官网域名。"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def homepage_from_notice_url(url: str) -> str | None:
    if not url or not url.startswith("http"):
        return None
    if "mp.weixin.qq.com" in url:
        return None
    host = urlparse(url).netloc.lower()
    if not host or not (host.endswith(".edu.cn") or host.endswith(".ac.cn")):
        return None
    return f"https://{host}".rstrip("/")


def maybe_heal_college_homepage(
    university: str,
    college: str,
    college_type: str,
    notice_url: str,
    *,
    source: str = "forum_radar",
) -> bool:
    """定向搜索/官方直抓成功时，尝试更新学院域名库。"""
    from crawler.domain_overrides import get_homepage_override, set_homepage_override

    homepage = homepage_from_notice_url(notice_url)
    if not homepage:
        return False
    existing = get_homepage_override(university, college, college_type)
    if existing and existing.rstrip("/") == homepage.rstrip("/"):
        return False
    ok = set_homepage_override(
        university, college, college_type, homepage, source=source,
    )
    if ok:
        logger.info(
            "域名自修复 %s %s → %s (%s)",
            university, college, homepage, source,
        )
    return ok
