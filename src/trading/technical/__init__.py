"""
Technical Analysis Module

Deterministic technical analysis engine - no AI involvement.
H1 structure, M15 RSI, candle patterns, entry/SL/TP levels.
"""

from __future__ import annotations

from .indicators import rsi, atr, sma, ema, standard_deviation
from .structure import H1StructureAnalyzer, SwingPoint
from .patterns import CandlePatternClassifier, PatternConfig, DEFAULT_PATTERN_CONFIG
from .levels import (
    calculate_entry,
    calculate_sl,
    calculate_tp,
    calculate_risk_reward,
    calculate_position_size,
)
from .engine import TechnicalEngine, TechnicalConfig

__all__ = [
    "rsi",
    "atr",
    "sma",
    "ema",
    "standard_deviation",
    "H1StructureAnalyzer",
    "SwingPoint",
    "CandlePatternClassifier",
    "PatternConfig",
    "DEFAULT_PATTERN_CONFIG",
    "calculate_entry",
    "calculate_sl",
    "calculate_tp",
    "calculate_risk_reward",
    "calculate_position_size",
    "TechnicalEngine",
    "TechnicalConfig",
]