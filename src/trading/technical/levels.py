"""
Entry, Stop-Loss, and Take-Profit Level Calculation

Deterministic calculations based on technical structure.
No AI involvement - pure mathematical rules.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Tuple

from ..canonical import OHLCBar, H1Structure, Instrument


def _find_structure_level_above(
    price: Decimal,
    structure: H1Structure,
    min_distance: Decimal,
) -> Decimal:
    """Find nearest structure level above price with minimum distance."""
    candidates = []

    # Check strong highs
    for level in structure.strong_highs:
        if level.price > price + min_distance:
            candidates.append(level.price)

    # Check equal highs
    for level in structure.equal_highs:
        if level.price > price + min_distance:
            candidates.append(level.price)

    if candidates:
        return min(candidates)  # Nearest level above

    # Fallback: use min_distance
    return price + min_distance


def _find_structure_level_below(
    price: Decimal,
    structure: H1Structure,
    min_distance: Decimal,
) -> Decimal:
    """Find nearest structure level below price with minimum distance."""
    candidates = []

    # Check strong lows
    for level in structure.strong_lows:
        if level.price < price - min_distance:
            candidates.append(level.price)

    # Check equal lows
    for level in structure.equal_lows:
        if level.price < price - min_distance:
            candidates.append(level.price)

    if candidates:
        return max(candidates)  # Nearest level below

    # Fallback
    return price - min_distance


def calculate_entry(
    signal_high: Decimal,
    signal_low: Decimal,
    direction: str,
    point_value: Decimal,
    buffer_points: int = 30,
) -> Decimal:
    """
    Calculate entry stop price.
    
    BUY stop: signal_high + buffer_points * point_value
    SELL stop: signal_low - buffer_points * point_value
    
    Args:
        signal_high: High of signal candle
        signal_low: Low of signal candle
        direction: "BUY" or "SELL"
        point_value: Minimum price movement for instrument
        buffer_points: Number of points to add/subtract (default 30)
        
    Returns:
        Entry price level
    """
    buffer = Decimal(buffer_points) * point_value

    if direction == "BUY":
        return signal_high + buffer
    elif direction == "SELL":
        return signal_low - buffer
    else:
        raise ValueError(f"Invalid direction: {direction}")


def calculate_sl(
    signal_candle: OHLCBar,
    direction: str,
    buffer_points: int = 10,
    point_value: Decimal = Decimal("0.00001"),
    liquidity_levels: Optional[list[Decimal]] = None,
) -> Decimal:
    """
    Calculate stop-loss level.
    
    SL placed at opposite side of signal candle + buffer.
    Considers nearby liquidity levels (equal highs/lows).
    
    BUY: SL below signal candle low - buffer
    SELL: SL above signal candle high + buffer
    
    Args:
        signal_candle: The signal candle
        direction: "BUY" or "SELL"
        buffer_points: Buffer in points
        point_value: Instrument point value
        liquidity_levels: Nearby liquidity levels to avoid
        
    Returns:
        Stop-loss price
    """
    buffer = Decimal(buffer_points) * point_value

    if direction == "BUY":
        sl = signal_candle.low - buffer
    elif direction == "SELL":
        sl = signal_candle.high + buffer
    else:
        raise ValueError(f"Invalid direction: {direction}")

    # Adjust SL to avoid liquidity levels
    if liquidity_levels:
        if direction == "BUY":
            # SL should be below liquidity levels
            for level in liquidity_levels:
                if level < sl and level > signal_candle.low - Decimal("100") * point_value:
                    sl = level - buffer
        else:
            # SL should be above liquidity levels
            for level in liquidity_levels:
                if level > sl and level < signal_candle.high + Decimal("100") * point_value:
                    sl = level + buffer

    return sl


def calculate_tp(
    entry: Decimal,
    sl: Decimal,
    direction: str,
    structure: H1Structure,
    risk_reward: Decimal = Decimal("2.0"),
) -> Tuple[Decimal, Decimal]:
    """
    Calculate TP1 and TP2 levels.
    
    TP1: Based on H1 structure level in direction of trade
    TP2: Further structure level
    Minimum RR: 1:2 (to TP1)
    
    Args:
        entry: Entry price
        sl: Stop loss price
        direction: "BUY" or "SELL"
        structure: H1 market structure
        risk_reward: Minimum risk/reward ratio
        
    Returns:
        (TP1, TP2) tuple
    """
    risk = abs(entry - sl)
    min_tp1_distance = risk * risk_reward

    if direction == "BUY":
        # TP1 at next resistance level above entry
        tp1 = _find_structure_level_above(entry, structure, min_tp1_distance)
        # TP2 at next resistance above TP1
        tp2 = _find_structure_level_above(tp1, structure, risk)
    else:
        # TP1 at next support level below entry
        tp1 = _find_structure_level_below(entry, structure, min_tp1_distance)
        # TP2 at next support below TP1
        tp2 = _find_structure_level_below(tp1, structure, risk)

    return tp1, tp2


def calculate_risk_reward(
    entry: Decimal,
    sl: Decimal,
    tp1: Decimal,
    direction: str,
) -> Decimal:
    """
    Calculate risk/reward ratio to TP1.
    
    RR = (TP1 - Entry) / |Entry - SL| for BUY
    RR = (Entry - TP1) / |Entry - SL| for SELL
    
    Args:
        entry: Entry price
        sl: Stop loss price
        tp1: Take profit 1 price
        direction: "BUY" or "SELL"
        
    Returns:
        Risk/reward ratio
    """
    risk = abs(entry - sl)
    if risk == 0:
        return Decimal("0")

    if direction == "BUY":
        reward = tp1 - entry
    else:
        reward = entry - tp1

    if reward <= 0:
        return Decimal("0")

    return (reward / risk).quantize(Decimal("0.01"))


def calculate_position_size(
    account_equity: Decimal,
    risk_pct: Decimal,
    entry: Decimal,
    sl: Decimal,
    contract_size: Decimal,
    tick_value: Decimal,
    account_currency: str = "USD",
) -> Decimal:
    """
    Calculate position size based on risk percentage.
    
    Position Size = (Account Equity * Risk %) / |Entry - SL| / Tick Value
    
    Args:
        account_equity: Total account equity
        risk_pct: Risk percentage (e.g., 0.01 for 1%)
        entry: Entry price
        sl: Stop loss price
        contract_size: Contract size per lot
        tick_value: Value per tick in account currency
        account_currency: Account currency
        
    Returns:
        Position size in lots
    """
    risk_amount = account_equity * risk_pct
    stop_distance = abs(entry - sl)

    if stop_distance == 0:
        return Decimal("0")

    # Position size = risk_amount / stop_distance / tick_value
    position_size = risk_amount / stop_distance / tick_value

    # Round to 2 decimal places (standard lot precision)
    return position_size.quantize(Decimal("0.01"))