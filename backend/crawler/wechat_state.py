"""搜狗微信搜索频次与每日学院扫描状态（JSON 持久化）。"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "wechat_sogou_state.json"


def _today() -> str:
    return date.today().isoformat()


def _week_key() -> str:
    d = date.today()
    return f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"


def load_state() -> dict:
    if not STATE_FILE.is_file():
        return {"daily": {}, "weekly": {}, "colleges": {}, "captcha_days": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("wechat state load failed: %s", e)
        return {"daily": {}, "weekly": {}, "colleges": {}, "captcha_days": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def sogou_daily_count() -> int:
    state = load_state()
    return int(state.get("daily", {}).get(_today(), 0))


def sogou_weekly_count() -> int:
    state = load_state()
    return int(state.get("weekly", {}).get(_week_key(), 0))


def should_use_sogou(*, force: bool = False) -> bool:
    if not settings.wechat_sogou_enabled and not force:
        return False
    if not settings.wechat_enabled:
        return False
    if force:
        return True
    if sogou_daily_count() >= settings.wechat_sogou_daily_limit:
        logger.info("搜狗微信：已达日限额 %d", settings.wechat_sogou_daily_limit)
        return False
    if sogou_weekly_count() >= settings.wechat_sogou_weekly_limit:
        logger.info("搜狗微信：已达周限额 %d", settings.wechat_sogou_weekly_limit)
        return False
    return True


def college_searched_today(university: str, college: str) -> bool:
    key = f"{university}|{college}"
    state = load_state()
    return state.get("colleges", {}).get(key) == _today()


def record_sogou_search(university: str, college: str, *, captcha: bool = False) -> None:
    state = load_state()
    today = _today()
    week = _week_key()

    daily = state.setdefault("daily", {})
    daily[today] = int(daily.get(today, 0)) + 1

    weekly = state.setdefault("weekly", {})
    weekly[week] = int(weekly.get(week, 0)) + 1

    key = f"{university}|{college}"
    state.setdefault("colleges", {})[key] = today

    if captcha:
        days: list = state.setdefault("captcha_days", [])
        if today not in days:
            days.append(today)
        days[:] = days[-30:]

    # 清理 14 天前的 daily 计数
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    state["daily"] = {k: v for k, v in daily.items() if k >= cutoff}

    save_state(state)


def record_sogou_failure(university: str, college: str, reason: str) -> None:
    logger.warning("搜狗微信搜索失败 [%s %s]: %s", university, college, reason)
