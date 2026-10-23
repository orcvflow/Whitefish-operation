"""
Decision Module

Deterministic decision engine - OPEN/WAIT/SKIP.
Combines fundamental + technical + risk analysis.
"""

from __future__ import annotations

from .engine import DecisionEngine

__all__ = [
    "DecisionEngine",
]