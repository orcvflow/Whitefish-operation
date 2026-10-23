"""
API Module

Minimal REST API for trading MVP monitoring.
Endpoints: /health, /metrics, /market/{symbol}, /candles/{symbol}, /news, /calendar, /analysis/{symbol}, /decisions, /decisions/{decision_id}
"""

from __future__ import annotations

from .server import create_app

__all__ = [
    "create_app",
]