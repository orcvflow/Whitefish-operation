"""
Risk Module

Hard risk gates - AI cannot override these.
Spread, data quality, market hours, news blackout, RR, daily limits.
"""

from __future__ import annotations

from .engine import RiskEngine, BlackoutManager

__all__ = [
    "RiskEngine",
    "BlackoutManager",
]