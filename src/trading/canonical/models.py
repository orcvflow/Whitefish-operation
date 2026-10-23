"""
Canonical Internal Models (Frozen Dataclasses)

Immutable DTOs for all internal data structures.
All financial values use Decimal for precision.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .enums import (
    Instrument,
    Timeframe,
    QualityStatus,
    DecisionStatus,
    TrendDirection,
    SwingStrength,
    CandlePatternType,
    NewsImpact,
    VerificationStatus,
    GateType,
)


# =============================================================================
# Instrument Metadata
# =============================================================================

@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Instrument specification from provider."""
    symbol: Instrument
    provider_symbol: str
    description: str
    precision: int                    # Decimal places
    point_value: Decimal             # Minimum price movement
    contract_size: Decimal           # Contract/lot size
    tick_value: Decimal              # Value per tick in account currency
    trading_hours: dict[str, str]    # e.g., {"open": "22:00", "close": "21:00"} UTC
    timezone: str = "UTC"


# =============================================================================
# Raw Event (before canonicalization)
# =============================================================================

@dataclass(frozen=True, slots=True)
class RawEvent:
    """Base raw event from any provider."""
    event_id: uuid.UUID
    provider: str
    provider_symbol: str
    provider_ts: datetime
    received_ts: datetime
    raw_payload_hash: str


@dataclass(frozen=True, slots=True)
class RawTickEvent(RawEvent):
    """Raw tick event before canonicalization."""
    bid: Decimal
    ask: Decimal
    mid: Decimal
    spread: Decimal
    high: Decimal
    low: Decimal
    volume: int
    direction: str
    day_diff_percent: Decimal
    market_state: str
    stale: bool
    quote_age_seconds: int
    source: str
    sequence: Optional[int] = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class RawOHLCEvent(RawEvent):
    """Raw OHLC event before canonicalization."""
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    tick_volume: int
    is_open: bool
    source: str


@dataclass(frozen=True, slots=True)
class RawNewsEvent(RawEvent):
    """Raw news event before canonicalization."""
    title: str
    url: str
    publisher: str
    published_at: datetime
    description: str
    image_url: Optional[str]
    category: Optional[str]
    language: str
    country: str
    instrument_tags: list[Instrument]
    content_hash: str


@dataclass(frozen=True, slots=True)
class RawCalendarEvent(RawEvent):
    """Raw calendar event before canonicalization."""
    ts: int
    time: datetime
    period: datetime
    country_code: str
    currency: str
    name: str
    impact: NewsImpact
    event_type: str
    sector: str
    unit: str
    multiplier: str
    digits: int
    actual: Optional[Decimal]
    forecast: Optional[Decimal]
    previous: Optional[Decimal]
    revised_previous: Optional[Decimal]
    revision: int
    time_mode: str
    source_url: str


# =============================================================================
# Canonical Data (validated, normalized)
# =============================================================================

@dataclass(frozen=True, slots=True)
class CanonicalTick:
    """Validated, normalized tick data."""
    event_id: uuid.UUID
    provider: str
    provider_symbol: str
    canonical_symbol: Instrument
    provider_ts: datetime
    received_ts: datetime
    persisted_ts: datetime
    bid: Decimal
    ask: Decimal
    mid: Decimal
    spread: Decimal
    high: Decimal
    low: Decimal
    volume: int
    direction: str
    day_diff_percent: Decimal
    market_state: str
    stale: bool
    quote_age_seconds: int
    source: str
    sequence: Optional[int]
    raw_payload_hash: str
    schema_version: int
    quality: QualityStatus

    def __post_init__(self) -> None:
        """Validate invariants after initialization."""
        if self.bid > self.mid:
            raise ValueError(f"bid ({self.bid}) > mid ({self.mid})")
        if self.ask < self.mid:
            raise ValueError(f"ask ({self.ask}) < mid ({self.mid})")
        if self.spread != (self.ask - self.bid):
            raise ValueError(f"spread mismatch: {self.spread} != {self.ask - self.bid}")
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) < low ({self.low})")


@dataclass(frozen=True, slots=True)
class OHLCBar:
    """Validated OHLC bar with invariants enforced."""
    symbol: Instrument
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    tick_volume: int
    is_open: bool
    source: str
    revision: int
    quality: QualityStatus
    provider_bar_id: Optional[str] = None  # For comparison with provider OHLC

    def __post_init__(self) -> None:
        """Enforce OHLC invariants."""
        if self.high < max(self.open, self.close):
            raise ValueError(
                f"OHLC invariant violated: high ({self.high}) < max(open={self.open}, close={self.close})"
            )
        if self.low > min(self.open, self.close):
            raise ValueError(
                f"OHLC invariant violated: low ({self.low}) > min(open={self.open}, close={self.close})"
            )
        if self.high < self.low:
            raise ValueError(f"OHLC invariant violated: high ({self.high}) < low ({self.low})")
        if self.open_time >= self.close_time:
            raise ValueError(f"open_time >= close_time")

    @property
    def body(self) -> Decimal:
        """Candle body size (abs(close - open))."""
        return abs(self.close - self.open)

    @property
    def range_(self) -> Decimal:
        """Full candle range (high - low)."""
        return self.high - self.low

    @property
    def upper_wick(self) -> Decimal:
        """Upper wick size."""
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> Decimal:
        """Lower wick size."""
        return min(self.open, self.close) - self.low

    @property
    def body_ratio(self) -> Decimal:
        """Body / range ratio (0-1)."""
        if self.range_ == 0:
            return Decimal("0")
        return self.body / self.range_

    @property
    def is_bullish(self) -> bool:
        """True if close > open."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """True if close < open."""
        return self.close < self.open

    @property
    def is_doji(self) -> bool:
        """True if body is very small (< 10% of range)."""
        return self.body_ratio < Decimal("0.1")


@dataclass(frozen=True, slots=True)
class NewsItem:
    """Validated news item."""
    id: uuid.UUID
    provider: str
    title: str
    url: str
    publisher: str
    published_at: datetime
    received_at: datetime
    description: str
    image_url: Optional[str]
    category: Optional[str]
    language: str
    country: str
    instrument_tags: list[Instrument]
    content_hash: str
    quality: QualityStatus

    def __post_init__(self) -> None:
        """Validate content hash."""
        import hashlib
        expected = hashlib.sha256(f"{self.title}{self.url}".encode()).hexdigest()[:16]
        if self.content_hash != expected:
            raise ValueError(f"Content hash mismatch: {self.content_hash} != {expected}")


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """Validated economic calendar event."""
    id: uuid.UUID
    provider: str
    event_id: str  # Provider's event ID (e.g., "mql5:840")
    ts: int
    time: datetime
    period: datetime
    country_code: str
    currency: str
    name: str
    impact: NewsImpact
    event_type: str
    sector: str
    unit: str
    multiplier: str
    digits: int
    actual: Optional[Decimal]
    forecast: Optional[Decimal]
    previous: Optional[Decimal]
    revised_previous: Optional[Decimal]
    revision: int
    time_mode: str
    source_url: str
    quality: QualityStatus

    @property
    def is_published(self) -> bool:
        """True if actual value is available."""
        return self.actual is not None

    @property
    def surprise(self) -> Optional[Decimal]:
        """Actual - forecast (if both available)."""
        if self.actual is not None and self.forecast is not None:
            return self.actual - self.forecast
        return None


# =============================================================================
# Analysis Snapshots (immutable context for AI/engines)
# =============================================================================

@dataclass(frozen=True, slots=True)
class MarketState:
    """Current market state snapshot."""
    symbol: Instrument
    timestamp: datetime
    tick: CanonicalTick
    spread_pct: Decimal
    session: str  # "asian", "london", "ny", "overlap"


@dataclass(frozen=True, slots=True)
class FundamentalSnapshot:
    """Compact immutable context for fundamental AI analysis."""
    snapshot_id: uuid.UUID
    timestamp: datetime
    symbol: Instrument
    tick_ref: CanonicalTick
    news_refs: list[NewsItem]
    calendar_refs: list[CalendarEvent]
    technical_ref: Optional["TechnicalAnalysis"] = None
    quality: QualityStatus = QualityStatus.CLEAN


# =============================================================================
# Fundamental Analysis Output
# =============================================================================

@dataclass(frozen=True, slots=True)
class CausalLink:
    """Single link in causal chain."""
    step: int
    description: str
    evidence: str  # Source reference
    confidence: str  # "proven" | "likely" | "speculative"


@dataclass(frozen=True, slots=True)
class InstrumentImpact:
    """Per-instrument impact assessment."""
    symbol: Instrument
    direction: str  # "bullish" | "bearish" | "neutral" | "mixed"
    magnitude: str  # "strong" | "moderate" | "weak"
    reasoning: str
    key_levels: list[Decimal] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FundamentalThesis:
    """Structured fundamental analysis output."""
    thesis: str
    sources: list[str]  # URLs
    causal_chain: list[CausalLink]
    per_instrument_impact: list[InstrumentImpact]
    counter_arguments: list[str]
    invalidation_conditions: list[str]
    uncertainty_assessment: str
    model_id: str
    model_provider: str
    prompt_version: str
    generated_at: datetime

    def get_impact(self, symbol: Instrument) -> Optional[InstrumentImpact]:
        """Get impact for specific instrument."""
        for imp in self.per_instrument_impact:
            if imp.symbol == symbol:
                return imp
        return None


# =============================================================================
# Technical Analysis Structures
# =============================================================================

@dataclass(frozen=True, slots=True)
class PriceLevel:
    """Price level with metadata."""
    price: Decimal
    strength: SwingStrength
    test_count: int
    last_test: datetime
    volume_at_level: Decimal
    type: str  # "high" | "low" | "equal_high" | "equal_low"


@dataclass(frozen=True, slots=True)
class Zone:
    """Price zone (support/resistance area)."""
    top: Decimal
    bottom: Decimal
    type: str  # "top" | "bottom" | "middle"
    strength: Decimal  # 0-1


@dataclass(frozen=True, slots=True)
class H1Structure:
    """H1 market structure analysis."""
    strong_highs: list[PriceLevel]
    strong_lows: list[PriceLevel]
    weak_highs: list[PriceLevel]
    weak_lows: list[PriceLevel]
    equal_highs: list[PriceLevel]
    equal_lows: list[PriceLevel]
    trend: TrendDirection
    top_zone: Zone
    bottom_zone: Zone
    middle_zone: Zone
    current_price: Decimal
    timestamp: datetime
    atr: Decimal


@dataclass(frozen=True, slots=True)
class CandlePattern:
    """Classified candle pattern."""
    pattern_type: CandlePatternType
    bullish: bool
    body_ratio: Decimal
    upper_wick_ratio: Decimal
    lower_wick_ratio: Decimal
    confirmation_required: bool
    bar_index: int  # Index in M15 series


@dataclass(frozen=True, slots=True)
class TechnicalAnalysis:
    """Complete technical analysis output."""
    symbol: Instrument
    timestamp: datetime
    h1_structure: H1Structure
    m15_rsi: Decimal
    m15_atr: Decimal
    last_closed_m15_bar: OHLCBar
    pattern: Optional[CandlePattern]
    entry_trigger: Optional[Decimal]  # Price level for entry stop
    entry_direction: Optional[str]  # "BUY" | "SELL"
    sl: Optional[Decimal]
    tp1: Optional[Decimal]
    tp2: Optional[Decimal]
    rr: Optional[Decimal]  # Risk/Reward to TP1
    spread: Decimal
    risk_checks: dict[str, bool]  # Individual risk gate results
    data_quality: QualityStatus


# =============================================================================
# Risk & Decision
# =============================================================================

@dataclass(frozen=True, slots=True)
class RiskCheckResult:
    """Result of a single risk gate check."""
    gate: GateType
    passed: bool
    message: str
    value: Optional[Decimal] = None
    threshold: Optional[Decimal] = None


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Context for risk evaluation."""
    symbol: Instrument
    account_equity: Decimal
    daily_pnl: Decimal
    trades_today: int
    technical: TechnicalAnalysis
    fundamental: FundamentalThesis
    market_state: MarketState
    config: "RiskConfig"  # Forward reference


@dataclass(frozen=True, slots=True)
class Decision:
    """Final trading decision with full audit trail."""
    decision_id: uuid.UUID
    snapshot_id: uuid.UUID
    symbol: Instrument
    status: DecisionStatus
    timestamp: datetime
    news_ids: list[uuid.UUID]
    calendar_ids: list[uuid.UUID]
    entry: Optional[Decimal]
    sl: Optional[Decimal]
    tp1: Optional[Decimal]
    tp2: Optional[Decimal]
    risk_pct: Optional[Decimal]
    rr: Optional[Decimal]
    failed_gates: list[GateType]
    strategy_version: str
    data_quality: QualityStatus
    axle_verification: dict[str, VerificationStatus]
    fundamental_thesis_id: Optional[uuid.UUID] = None
    technical_analysis_id: Optional[uuid.UUID] = None
    position_size: Optional[Decimal] = None
    notes: str = ""


# =============================================================================
# AXLE Verification
# =============================================================================

@dataclass(frozen=True, slots=True)
class VerificationResult:
    """AXLE verification result."""
    check_name: str
    status: VerificationStatus
    lean_code: Optional[str] = None
    lean_output: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0


# =============================================================================
# Configuration Models
# =============================================================================

@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Risk configuration."""
    default_risk_pct: Decimal = Decimal("0.01")
    max_trade_risk_pct: Decimal = Decimal("0.02")
    daily_loss_limit_pct: Decimal = Decimal("0.03")
    max_trades_per_day: int = 3
    min_risk_reward: Decimal = Decimal("2.0")
    max_spread_pct: dict[Instrument, Decimal] = field(default_factory=dict)
    entry_buffer_points: int = 30
    sl_buffer_points: int = 10


@dataclass(frozen=True, slots=True)
class TechnicalConfig:
    """Technical analysis configuration."""
    h1_timeframe: Timeframe = Timeframe.H1
    m15_timeframe: Timeframe = Timeframe.M15
    rsi_period: int = 14
    rsi_overbought: int = 70
    rsi_oversold: int = 30
    swing_lookback: int = 5
    equal_level_tolerance_atr_mult: Decimal = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class AIConfig:
    """AI model configuration."""
    provider: str = "openrouter"
    model: str = "inclusionAI/Ling-3.0-flash-Fin"
    temperature: float = 1.0
    top_p: float = 0.95
    top_k: int = 20
    max_tokens: int = 8192
    timeout_seconds: int = 30
    max_retries: int = 2


@dataclass(frozen=True, slots=True)
class BlackoutConfig:
    """News blackout configuration."""
    minutes_before: int = 30
    minutes_after: int = 30
    high_impact_only: bool = True
    currencies: list[str] = field(default_factory=lambda: ["USD", "EUR", "GBP"])
    instrument_events: dict[Instrument, list[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QualityConfig:
    """Data quality thresholds."""
    max_quote_age_seconds: int = 300
    max_ohlc_gap_minutes: int = 120
    min_tick_volume: int = 1
    stale_data_action: str = "skip"


@dataclass(frozen=True, slots=True)
class PersistenceConfig:
    """Persistence configuration."""
    db_path: str = "./data/trading.db"
    journal_path: str = "./data/journal.log"
    journal_max_size_mb: int = 100
    journal_rotation: str = "daily"
    backup_interval_hours: int = 6
    backup_retention_days: int = 30
    wal_mode: bool = True
    sync_on_write: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application configuration."""
    instruments: list[Instrument] = field(default_factory=lambda: [
        Instrument.XAUUSD, Instrument.EURUSD, Instrument.GBPUSD, Instrument.WTI
    ])
    instrument_specs: dict[Instrument, InstrumentSpec] = field(default_factory=dict)
    risk: RiskConfig = field(default_factory=RiskConfig)
    technical: TechnicalConfig = field(default_factory=TechnicalConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    blackout: BlackoutConfig = field(default_factory=BlackoutConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)