"""学院通知发布日历：窗口外不发起爬虫请求。"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_CALENDAR_FILE = Path(__file__).resolve().parent.parent / "data" / "notice_calendar.json"


def _load_calendar() -> dict:
    if not _CALENDAR_FILE.exists():
        return {}
    try:
        return json.loads(_CALENDAR_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取 notice_calendar.json 失败: %s", e)
        return {}


def _parse_mmdd(s: str, year: int) -> date:
    m, d = s.split("-")
    return date(year, int(m), int(d))


def window_for(
    university: str,
    college_type: str,
    board: str,
    *,
    year: int | None = None,
) -> tuple[date, date, int]:
    """返回 (check_start, check_end, retry_interval_days)。"""
    year = year or date.today().year
    cal = _load_calendar()
    board_cfg = cal.get("default", {}).get(board, {})
    cfg = dict(board_cfg.get(college_type, board_cfg.get("law", {})))
    uni_override = cal.get("overrides", {}).get(university, {}).get(board, {})
    cfg.update(uni_override)

    start = _parse_mmdd(cfg.get("check_start", "07-01"), year)
    end = _parse_mmdd(cfg.get("check_end", "08-20"), year)
    interval = int(cfg.get("retry_interval_days", 1))
    return start, end, interval


def in_activation_window(
    university: str,
    college_type: str,
    board: str,
    *,
    today: date | None = None,
) -> bool:
    today = today or date.today()
    start, end, _ = window_for(university, college_type, board, year=today.year)
    return start <= today <= end


def next_check_datetime(
    university: str,
    college_type: str,
    board: str,
    *,
    today: date | None = None,
) -> datetime:
    """窗口未到 → 返回窗口起始日；窗口内 → 明天。"""
    today = today or date.today()
    start, end, interval = window_for(university, college_type, board, year=today.year)
    if today < start:
        return datetime.combine(start, datetime.min.time())
    if today > end:
        return datetime.combine(start.replace(year=start.year + 1), datetime.min.time())
    return datetime.now() + timedelta(days=max(1, interval))
