"""Latency and overlap reporting (contracts §13). Pure logic, no I/O."""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass

OVERLAP_SECS = 0.150


def pct(values: list[float], q: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def summarise(latencies_secs: Iterable[float | None]) -> LatencySummary:
    answered: list[float] = []
    overlapped = 0
    unanswered = 0
    for v in latencies_secs:
        if v is None:
            unanswered += 1
        elif v < OVERLAP_SECS:
            overlapped += 1
        else:
            answered.append(v)
    return LatencySummary(answered=answered, overlapped=overlapped, unanswered=unanswered)


@dataclass(frozen=True)
class LatencySummary:
    answered: list[float]
    overlapped: int
    unanswered: int

    @property
    def p50(self) -> float | None:
        return pct(self.answered, 50)

    @property
    def p90(self) -> float | None:
        return pct(self.answered, 90)

    @property
    def overlap_rate(self) -> float | None:
        denom = len(self.answered) + self.overlapped
        if denom == 0:
            return None
        return 100 * self.overlapped / denom

    def format(self, kind: str) -> str:
        p50 = "-" if self.p50 is None else f"{self.p50 * 1000:.0f}"
        p90 = "-" if self.p90 is None else f"{self.p90 * 1000:.0f}"
        n = len(self.answered)
        # The server cannot see overlaps, and with no turns there is no rate: "-", never 0%.
        rate = None if kind == "server" else self.overlap_rate
        overlap_s = "-" if rate is None else f"{round(rate)}%"
        line = (
            f"{kind}: p50 {p50} / p90 {p90} ms "
            f"(n = {n} answered, overlap {overlap_s}, {self.unanswered} unanswered)"
        )
        if kind == "server":
            line += " (overlap not measured at the server)"
        return line
