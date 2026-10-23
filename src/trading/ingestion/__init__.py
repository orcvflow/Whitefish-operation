"""
Ingestion Module

Market data, news, and calendar ingestion adapters.
"""

from __future__ import annotations

from .biquote_rest import BiquoteRESTClient, BiquoteOHLCRestClient, BiquoteNewsClient, BiquoteCalendarClient
from .biquote_ws import BiquoteWebSocketClient, IngestionPipeline

__all__ = [
    "BiquoteRESTClient",
    "BiquoteOHLCRestClient",
    "BiquoteNewsClient",
    "BiquoteCalendarClient",
    "BiquoteWebSocketClient",
    "IngestionPipeline",
]