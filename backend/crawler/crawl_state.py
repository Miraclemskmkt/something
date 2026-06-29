"""后台检索任务状态（按板块 + 分层独立）。"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from tier_filter import VALID_TIERS

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_states: dict[str, "CrawlJobState"] = {}


@dataclass
class CrawlJobState:
    running: bool = False
    board: str = ""
    tier: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_message: str = ""
    last_result: dict = field(default_factory=dict)


def job_key(board: str, tier: str | None = None) -> str:
    if tier and tier in VALID_TIERS:
        return f"{board}:{tier}"
    return board


def get_lock(board: str, tier: str | None = None) -> asyncio.Lock:
    return _locks[job_key(board, tier)]


def get_state(board: str, tier: str | None = None) -> CrawlJobState:
    key = job_key(board, tier)
    if key not in _states:
        _states[key] = CrawlJobState()
    return _states[key]


def is_running(board: str, tier: str | None = None) -> bool:
    return get_state(board, tier).running


def mark_started(board: str, tier: str | None = None):
    st = get_state(board, tier)
    st.running = True
    st.board = board
    st.tier = tier or ""
    st.started_at = datetime.now()
    st.finished_at = None
    label = tier or "全部"
    st.last_message = f"正在检索{label}院校..."


def mark_progress(board: str, message: str, tier: str | None = None):
    st = get_state(board, tier)
    if st.running:
        st.last_message = message


def mark_finished(board: str, result: dict, tier: str | None = None):
    st = get_state(board, tier)
    st.running = False
    st.finished_at = datetime.now()
    st.last_result = result
    st.last_message = result.get("message", "")


def mark_failed(board: str, message: str, tier: str | None = None):
    st = get_state(board, tier)
    st.running = False
    st.finished_at = datetime.now()
    st.last_message = message


def status_payload(board: str, tier: str | None = None) -> dict:
    st = get_state(board, tier)
    return {
        "board": board,
        "tier": tier or st.tier or "",
        "running": st.running,
        "started_at": st.started_at.isoformat() if st.started_at else None,
        "finished_at": st.finished_at.isoformat() if st.finished_at else None,
        "message": st.last_message,
        "last_result": st.last_result,
    }
