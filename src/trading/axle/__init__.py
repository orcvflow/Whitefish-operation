"""
AXLE Module

Lean 4 mathematical verification via axiom-axle Python client.
Verifies OHLC invariants, RSI formula, RR calculations, position sizing.
Non-blocking - if AXLE unavailable, continues with Python tests only.
"""

from __future__ import annotations

from .verifier import AXLEVerifier

__all__ = [
    "AXLEVerifier",
]