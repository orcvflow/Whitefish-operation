"""
Canonical Validation

Validation functions for all canonical data types.
Enforces invariants, quality checks, and data integrity.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from .models import (
    CanonicalTick,
    OHLCBar,
    NewsItem,
    CalendarEvent,
    Instrument,
    QualityStatus,
    Timeframe,
)
from .enums import NewsImpact


# =============================================================================
# OHLC Validation
# =============================================================================

def validate_ohlc_invariants(bar: OHLCBar) -> list[str]:
    """
    Validate OHLC mathematical invariants.
    Returns list of violations (empty if all pass).
    """
    violations = []
    
    if bar.high < max(bar.open, bar.close):
        violations.append(
            f"H < max(O,C): {bar.high} < max({bar.open}, {bar.close})"
        )
    
    if bar.low > min(bar.open, bar.close):
        violations.append(
            f"L > min(O,C): {bar.low} > min({bar.open}, {bar.close})"
        )
    
    if bar.high < bar.low:
        violations.append(f"H < L: {bar.high} < {bar.low}")
    
    if bar.open_time >= bar.close_time:
        violations.append(f"open_time >= close_time")
    
    if bar.volume < 0:
        violations.append(f"negative volume: {bar.volume}")
    
    if bar.tick_volume < 0:
        violations.append(f"negative tick_volume: {bar.tick_volume}")
    
    return violations


def validate_ohlc_sequence(bars: list[OHLCBar]) -> list[str]:
    """
    Validate OHLC sequence continuity.
    Checks for gaps, overlaps, ordering.
    """
    violations = []
    
    if not bars:
        return violations
    
    # Sort by open_time
    sorted_bars = sorted(bars, key=lambda b: b.open_time)
    
    for i in range(1, len(sorted_bars)):
        prev = sorted_bars[i - 1]
        curr = sorted_bars[i]
        
        # Check for overlap
        if curr.open_time < prev.close_time:
            violations.append(
                f"Bar overlap: {curr.open_time} < {prev.close_time} "
                f"(index {i-1}->{i})"
            )
        
        # Check for gap (more than 1.5x expected interval)
        expected_interval = prev.close_time - prev.open_time
        actual_gap = curr.open_time - prev.close_time
        if actual_gap > expected_interval * 1.5:
            violations.append(
                f"Gap detected: {actual_gap} > 1.5x interval "
                f"(index {i-1}->{i})"
            )
    
    return violations


# =============================================================================
# Tick Validation
# =============================================================================

def validate_tick(tick: CanonicalTick, config_quote_age: int = 300) -> list[str]:
    """
    Validate canonical tick quality.
    Returns list of issues (empty if clean).
    """
    issues = []
    
    # Price sanity
    if tick.bid <= 0 or tick.ask <= 0 or tick.mid <= 0:
        issues.append("Non-positive price")
    
    if tick.bid > tick.mid:
        issues.append(f"bid > mid: {tick.bid} > {tick.mid}")
    
    if tick.ask < tick.mid:
        issues.append(f"ask < mid: {tick.ask} < {tick.mid}")
    
    # Spread check
    calc_spread = tick.ask - tick.bid
    if abs(calc_spread - tick.spread) > Decimal("0.00001"):
        issues.append(f"Spread mismatch: stored={tick.spread} calc={calc_spread}")
    
    # Quote age
    if tick.quote_age_seconds > config_quote_age:
        issues.append(f"Stale quote: {tick.quote_age_seconds}s > {config_quote_age}s")
    
    # Market state
    if tick.market_state == "closed":
        issues.append("Market closed")
    
    if tick.stale:
        issues.append("Provider marked stale")
    
    # High/low sanity
    if tick.high < tick.low:
        issues.append(f"Day high < low: {tick.high} < {tick.low}")
    
    return issues


def classify_tick_quality(issues: list[str]) -> QualityStatus:
    """Classify tick quality based on issues."""
    if not issues:
        return QualityStatus.CLEAN
    
    critical = {"Stale quote", "Market closed", "Non-positive price", "bid > mid", "ask < mid"}
    if any(issue in critical for issue in issues):
        return QualityStatus.STALE if "Stale quote" in issues or "Market closed" in issues else QualityStatus.INVALID_OHLC
    
    return QualityStatus.PARTIAL


# =============================================================================
# News Validation
# =============================================================================

def validate_news(item: NewsItem) -> list[str]:
    """Validate news item."""
    issues = []
    
    if not item.title or not item.title.strip():
        issues.append("Empty title")
    
    if not item.url or not item.url.startswith(("http://", "https://")):
        issues.append("Invalid URL")
    
    if not item.publisher:
        issues.append("Missing publisher")
    
    if item.published_at > item.received_at:
        issues.append("published_at > received_at (future dating)")
    
    # Check content hash
    expected = hashlib.sha256(f"{item.title}{item.url}".encode()).hexdigest()[:16]
    if item.content_hash != expected:
        issues.append(f"Content hash mismatch")
    
    # Recency
    age = datetime.utcnow() - item.published_at
    if age > timedelta(days=7):
        issues.append(f"Old news: {age.days} days")
    
    return issues


# =============================================================================
# Calendar Validation
# =============================================================================

def validate_calendar(event: CalendarEvent) -> list[str]:
    """Validate calendar event."""
    issues = []
    
    if not event.name:
        issues.append("Empty event name")
    
    if event.actual is not None and event.forecast is not None:
        # Both present - can calculate surprise
        pass
    
    if event.actual is not None and event.forecast is None:
        issues.append("Has actual but no forecast")
    
    # Time mode check
    if event.time_mode not in ("exact", "date", "notime", "tentative"):
        issues.append(f"Unknown time_mode: {event.time_mode}")
    
    if event.time_mode != "exact":
        issues.append(f"Non-exact time_mode: {event.time_mode}")
    
    return issues


# =============================================================================
# Symbol/Instrument Validation
# =============================================================================

INSTRUMENT_PATTERNS = {
    Instrument.XAUUSD: re.compile(r"^XAUUSD$", re.IGNORECASE),
    Instrument.EURUSD: re.compile(r"^EURUSD$", re.IGNORECASE),
    Instrument.GBPUSD: re.compile(r"^GBPUSD$", re.IGNORECASE),
    Instrument.WTI: re.compile(r"^(WTI|USOIL|CL)$", re.IGNORECASE),
}

def normalize_symbol(raw: str) -> Optional[Instrument]:
    """Normalize provider symbol to canonical Instrument."""
    raw_upper = raw.upper().strip()
    
    for inst, pattern in INSTRUMENT_PATTERNS.items():
        if pattern.match(raw_upper):
            return inst
    
    # Try direct match
    try:
        return Instrument(raw_upper)
    except ValueError:
        return None


def validate_instrument_precision(
    price: Decimal,
    instrument: Instrument,
    precision: int
) -> bool:
    """Check if price respects instrument precision."""
    # Get decimal places
    tuple_ = price.as_tuple()
    if tuple_.exponent < 0:
        decimal_places = -tuple_.exponent
        return decimal_places <= precision
    return True


def round_to_precision(price: Decimal, precision: int) -> Decimal:
    """Round price to instrument precision."""
    quantizer = Decimal("0.1") ** precision
    return price.quantize(quantizer)


# =============================================================================
# Timestamp Validation
# =============================================================================

def validate_utc_timestamp(ts: datetime, field_name: str = "timestamp") -> list[str]:
    """Validate timestamp is timezone-aware UTC."""
    issues = []
    
    if ts.tzinfo is None:
        issues.append(f"{field_name}: naive datetime (no timezone)")
    elif ts.tzinfo.utcoffset(ts) != timedelta(0):
        issues.append(f"{field_name}: not UTC (offset: {ts.tzinfo.utcoffset(ts)})")
    
    return issues


def ensure_utc(ts: datetime) -> datetime:
    """Ensure datetime is UTC timezone-aware."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=datetime.utcnow().astimezone().tzinfo)
    return ts.astimezone(datetime.utcnow().astimezone().tzinfo)


# =============================================================================
# Duplicate Detection
# =============================================================================

def make_duplicate_key(
    provider: str,
    provider_symbol: str,
    provider_ts: datetime
) -> str:
    """Create duplicate detection key."""
    ts_str = provider_ts.strftime("%Y%m%d%H%M%S%f")
    return f"{provider}:{provider_symbol}:{ts_str}"


# =============================================================================
# Gap Detection
# =============================================================================

def detect_time_gap(
    prev_ts: datetime,
    curr_ts: datetime,
    expected_interval: timedelta,
    tolerance_mult: float = 1.5
) -> Optional[timedelta]:
    """
    Detect gap between timestamps.
    Returns gap duration if gap > tolerance, else None.
    """
    gap = curr_ts - prev_ts
    if gap > expected_interval * tolerance_mult:
        return gap
    return None


def detect_sequence_gap(prev_seq: int, curr_seq: int) -> Optional[int]:
    """Detect sequence number gap."""
    if curr_seq <= prev_seq:
        return curr_seq - prev_seq  # Negative or zero = duplicate/out of order
    if curr_seq > prev_seq + 1:
        return curr_seq - prev_seq - 1  # Missing count
    return None


# =============================================================================
# Spread Validation
# =============================================================================

def validate_spread(
    spread: Decimal,
    instrument: Instrument,
    max_spread_pct: dict[Instrument, Decimal],
    mid: Decimal
) -> list[str]:
    """Validate spread against threshold."""
    issues = []
    
    if mid <= 0:
        issues.append("Invalid mid price for spread check")
        return issues
    
    spread_pct = spread / mid
    max_allowed = max_spread_pct.get(instrument, Decimal("0.001"))
    
    if spread_pct > max_allowed:
        issues.append(
            f"Spread {spread_pct:.6%} exceeds max {max_allowed:.6%} for {instrument.value}"
        )
    
    return issues


# =============================================================================
# Comprehensive Quality Check
# =============================================================================

def check_data_quality(
    tick: Optional[CanonicalTick] = None,
    ohlc: Optional[OHLCBar] = None,
    news: Optional[NewsItem] = None,
    calendar: Optional[CalendarEvent] = None,
    config_quote_age: int = 300,
    max_spread_pct: Optional[dict[Instrument, Decimal]] = None,
) -> QualityStatus:
    """
    Comprehensive quality check across all data types.
    Returns worst quality status found.
    """
    all_issues = []
    
    if tick:
        all_issues.extend(validate_tick(tick, config_quote_age))
    
    if ohlc:
        all_issues.extend(validate_ohlc_invariants(ohlc))
    
    if news:
        all_issues.extend(validate_news(news))
    
    if calendar:
        all_issues.extend(validate_calendar(calendar))
    
    if tick and max_spread_pct:
        all_issues.extend(validate_spread(tick.spread, tick.canonical_symbol, max_spread_pct, tick.mid))
    
    return classify_tick_quality(all_issues)