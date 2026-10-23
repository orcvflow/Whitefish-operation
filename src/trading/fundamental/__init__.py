"""
Fundamental Analysis Module

AI-driven news and economic calendar analysis using Ling-3.0-flash-Fin.
Produces structured fundamental thesis with causal chain and counter-arguments.
"""

from __future__ import annotations

from .ai_adapter import AIAdapter, OpenRouterAdapter, MockAdapter, create_ai_adapter, AIConfig, AIResponse
from .snapshot import FundamentalSnapshotBuilder, FundamentalPromptBuilder, FundamentalResponseParser
from .engine import FundamentalEngine

__all__ = [
    "AIAdapter",
    "OpenRouterAdapter",
    "MockAdapter",
    "create_ai_adapter",
    "AIConfig",
    "AIResponse",
    "FundamentalSnapshotBuilder",
    "FundamentalPromptBuilder",
    "FundamentalResponseParser",
    "FundamentalEngine",
]