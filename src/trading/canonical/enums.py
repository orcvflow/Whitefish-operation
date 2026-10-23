"""
Canonical Enumerations

All enum types used throughout the system.
"""

from __future__ import annotations
from enum import Enum, auto


class Instrument(str, Enum):
    """Supported trading instruments."""
    XAUUSD = "XAUUSD"
    EURUSD = "EURUSD"
    GBPUSD = "GBPUSD"
    WTI = "WTI"

    @property
    def base_currency(self) -> str:
        """Base currency of the pair."""
        return self.value[:3]

    @property
    def quote_currency(self) -> str:
        """Quote currency of the pair."""
        return self.value[3:]

    @property
    def is_metal(self) -> bool:
        """Check if instrument is a metal."""
        return self == Instrument.XAUUSD

    @property
    def is_forex(self) -> bool:
        """Check if instrument is forex."""
        return self in (Instrument.EURUSD, Instrument.GBPUSD)

    @property
    def is_commodity(self) -> bool:
        """Check if instrument is commodity."""
        return self == Instrument.WTI


class Timeframe(str, Enum):
    """Supported timeframes for OHLC data."""
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def minutes(self) -> int:
        """Timeframe in minutes."""
        mapping = {
            Timeframe.M1: 1,
            Timeframe.M5: 5,
            Timeframe.M15: 15,
            Timeframe.M30: 30,
            Timeframe.H1: 60,
            Timeframe.H4: 240,
            Timeframe.D1: 1440,
        }
        return mapping[self]

    @property
    def seconds(self) -> int:
        """Timeframe in seconds."""
        return self.minutes * 60


class QualityStatus(str, Enum):
    """Data quality status."""
    CLEAN = "clean"                    # All checks passed
    STALE = "stale"                    # Data older than threshold
    GAP_DETECTED = "gap_detected"      # Gap in sequence/time
    DUPLICATE = "duplicate"            # Duplicate event detected
    INVALID_OHLC = "invalid_ohlc"      # OHLC invariants violated
    HIGH_SPREAD = "high_spread"        # Spread exceeds threshold
    MISSING_FIELDS = "missing_fields"  # Required fields missing
    PROVIDER_ERROR = "provider_error"  # Provider returned error
    PARTIAL = "partial"                # Some checks passed, some failed


class DecisionStatus(str, Enum):
    """Trading decision status."""
    OPEN = "open"      # All gates passed, trade plan ready
    WAIT = "wait"      # Setup forming, trigger not hit
    SKIP = "skip"      # Gates failed, no trade


class TrendDirection(str, Enum):
    """Market trend direction."""
    UP = "up"
    DOWN = "down"
    RANGING = "ranging"
    UNKNOWN = "unknown"


class SwingStrength(str, Enum):
    """Swing point strength classification."""
    STRONG = "strong"
    WEAK = "weak"
    EQUAL = "equal"


class CandlePatternType(str, Enum):
    """Candle pattern types."""
    THREE_ONE = "3-1"    # Strong bullish
    ONE_ONE = "1-1"      # Small body, long wick (bottom zone)
    ONE_THREE = "1-3"    # Strong bearish
    THREE_THREE = "3-3"  # Long upper wick (top zone)
    NONE = "none"


class NewsImpact(str, Enum):
    """News/economic event impact level."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    HOLIDAY = "holiday"


class VerificationStatus(str, Enum):
    """AXLE verification status."""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    TIMEOUT = "timeout"


class GateType(str, Enum):
    """Risk gate types."""
    SPREAD = "spread"
    DATA_QUALITY = "data_quality"
    MARKET_HOURS = "market_hours"
    BLACKOUT = "blackout"
    RISK_REWARD = "risk_reward"
    DAILY_LIMITS = "daily_limits"
    STALE_DATA = "stale_data"