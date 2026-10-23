"""
Deterministic Technical Indicators

All calculations use Decimal precision for financial accuracy.
Wilder's smoothing for RSI/ATR (standard trading methodology).
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Optional

# Set decimal precision
from decimal import getcontext
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN


def rsi(
    closes: list[Decimal],
    period: int = 14,
) -> Optional[Decimal]:
    """
    Calculate RSI (Relative Strength Index) using Wilder's smoothing.
    
    Args:
        closes: List of closing prices (most recent last)
        period: RSI period (default 14)
        
    Returns:
        RSI value (0-100) or None if insufficient data
        
    RSI Formula (Wilder's):
    1. Calculate price changes: diff[i] = close[i] - close[i-1]
    2. Separate gains and losses
    3. Initial average gain/loss = simple average of first period values
    4. Subsequent: avg = (prev_avg * (period-1) + current) / period
    5. RS = avg_gain / avg_loss
    6. RSI = 100 - (100 / (1 + RS))
    
    Edge cases:
    - If avg_loss == 0: RSI = 100 (all gains)
    - If insufficient data: returns None
    """
    if len(closes) < period + 1:
        return None
    
    # Calculate price changes
    changes: list[Decimal] = []
    for i in range(1, len(closes)):
        changes.append(closes[i] - closes[i - 1])
    
    # Separate gains and losses
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for change in changes:
        if change > 0:
            gains.append(change)
            losses.append(Decimal("0"))
        elif change < 0:
            gains.append(Decimal("0"))
            losses.append(abs(change))
        else:
            gains.append(Decimal("0"))
            losses.append(Decimal("0"))
    
    # We need at least `period` changes
    if len(gains) < period:
        return None
    
    # Initial average (simple average of first `period` values)
    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)
    
    # Wilder's smoothing for remaining values
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * Decimal(period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + losses[i]) / Decimal(period)
    
    # Calculate RSI
    if avg_loss == Decimal("0"):
        return Decimal("100")
    
    rs = avg_gain / avg_loss
    rsi_value = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    
    # Round to 2 decimal places
    return rsi_value.quantize(Decimal("0.01"))


def atr(
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    period: int = 14,
) -> Optional[Decimal]:
    """
    Calculate ATR (Average True Range) using Wilder's smoothing.
    
    True Range = max(high - low, |high - prev_close|, |low - prev_close|)
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: ATR period (default 14)
        
    Returns:
        ATR value or None if insufficient data
    """
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None
    
    # Calculate True Ranges
    true_ranges: list[Decimal] = []
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(hl, hc, lc))
    
    if len(true_ranges) < period:
        return None
    
    # Initial ATR (simple average)
    avg_atr = sum(true_ranges[:period]) / Decimal(period)
    
    # Wilder's smoothing
    for i in range(period, len(true_ranges)):
        avg_atr = (avg_atr * Decimal(period - 1) + true_ranges[i]) / Decimal(period)
    
    return avg_atr


def sma(
    values: list[Decimal],
    period: int,
) -> Optional[Decimal]:
    """Simple Moving Average."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / Decimal(period)


def ema(
    values: list[Decimal],
    period: int,
    previous_ema: Optional[Decimal] = None,
) -> Decimal:
    """Exponential Moving Average."""
    if not values:
        raise ValueError("Values list cannot be empty")
    
    multiplier = Decimal("2") / Decimal(period + 1)
    
    if previous_ema is None:
        # First EMA = SMA of first `period` values
        if len(values) >= period:
            return sum(values[:period]) / Decimal(period)
        else:
            return values[0]
    
    return (values[-1] - previous_ema) * multiplier + previous_ema


def standard_deviation(
    values: list[Decimal],
    period: int,
) -> Optional[Decimal]:
    """Standard deviation over `period` values."""
    if len(values) < period:
        return None
    
    recent = values[-period:]
    mean = sum(recent) / Decimal(period)
    variance = sum((x - mean) ** 2 for x in recent) / Decimal(period)
    return variance.sqrt()