"""Bounded resource accounting for the handwritten research engine."""

from __future__ import annotations

import time
from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    """Raised when the engine cannot start another provider call."""


@dataclass
class Budget:
    mode: str = "time"
    limit: int = 900
    conclude_after: float = 0.99

    def __post_init__(self) -> None:
        if self.mode not in {"time", "tokens", "calls"}:
            raise ValueError("budget mode must be time, tokens, or calls")
        if self.limit <= 0:
            raise ValueError("budget limit must be positive")
        self.started_at = time.monotonic()
        self.calls = 0
        self.total_output_tokens = 0

    def reserve_call(self) -> None:
        if self.exhausted():
            raise BudgetExceeded(f"{self.mode} budget exhausted")
        self.calls += 1

    def record(self, response: dict | None) -> None:
        usage = (response or {}).get("usage", {})
        self.total_output_tokens += int(
            usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        )

    def used(self) -> int:
        if self.mode == "time":
            return int(time.monotonic() - self.started_at)
        if self.mode == "tokens":
            return self.total_output_tokens
        return self.calls

    def exhausted(self) -> bool:
        return self.used() >= self.limit

    def status(self) -> str:
        return f"{self.mode}: {self.used()}/{self.limit}"
