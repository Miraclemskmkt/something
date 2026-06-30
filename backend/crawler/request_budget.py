"""单学院 HTTP 请求预算与熔断。"""
from __future__ import annotations

import time


class CollegeBudget:
    """限制单学院请求数、搜索耗时与总耗时。"""

    __slots__ = (
        "max_requests",
        "search_budget_sec",
        "deadline",
        "search_deadline",
        "used_requests",
        "search_started",
    )

    def __init__(
        self,
        *,
        max_requests: int,
        total_sec: float,
        search_budget_sec: float,
    ) -> None:
        now = time.monotonic()
        self.max_requests = max_requests
        self.search_budget_sec = search_budget_sec
        self.deadline = now + total_sec
        self.search_deadline = now + search_budget_sec
        self.used_requests = 0
        self.search_started: float | None = None

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def search_expired(self) -> bool:
        if self.search_started is None:
            return time.monotonic() >= self.search_deadline
        return (time.monotonic() - self.search_started) >= self.search_budget_sec

    def can_request(self) -> bool:
        return not self.expired() and self.used_requests < self.max_requests

    def consume(self, n: int = 1) -> bool:
        if not self.can_request():
            return False
        self.used_requests += n
        return self.used_requests <= self.max_requests

    def begin_search(self) -> None:
        if self.search_started is None:
            self.search_started = time.monotonic()

    def remaining_sec(self) -> float:
        return max(0.0, self.deadline - time.monotonic())
