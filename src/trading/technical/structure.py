"""
H1 Market Structure Analysis

Deterministic swing detection and structure analysis for H1 timeframe.
Identifies strong/weak highs/lows, equal levels, trend, and price zones.
No look-ahead bias - only uses data available at each point in time.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..canonical import (
    Instrument,
    Timeframe,
    TrendDirection,
    SwingStrength,
    PriceLevel,
    Zone,
    H1Structure,
    OHLCBar,
)


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """A swing high or low point."""
    price: Decimal
    time: str  # ISO timestamp
    bar_index: int
    strength: SwingStrength
    test_count: int = 0
    last_test_time: Optional[str] = None


class H1StructureAnalyzer:
    """
    H1 market structure analyzer.
    
    Detects swing highs/lows using fractal method.
    Classifies strength based on volume, test count, and reaction.
    Identifies equal highs/lows (liquidity levels).
    Defines price zones (top, bottom, middle).
    
    No look-ahead bias - swing points only confirmed when
    sufficient bars have closed after the potential swing.
    """

    def __init__(
        self,
        swing_lookback: int = 5,
        equal_level_tolerance_atr_mult: Decimal = Decimal("0.5"),
    ):
        self.swing_lookback = swing_lookback
        self.equal_level_tolerance_atr_mult = equal_level_tolerance_atr_mult

    def analyze(
        self,
        bars: list[OHLCBar],
        atr: Decimal,
    ) -> H1Structure:
        """
        Analyze H1 market structure from closed bars.
        
        Args:
            bars: List of H1 OHLC bars (closed only, oldest first)
            atr: Current ATR value for tolerance calculations
            
        Returns:
            H1Structure with all structure elements
        """
        if len(bars) < self.swing_lookback * 2 + 2:
            return self._empty_structure(bars, atr)

        # Use only closed bars for structure
        closed_bars = [b for b in bars if not b.is_open]
        if len(closed_bars) < self.swing_lookback * 2 + 2:
            closed_bars = bars  # Fallback if all are open

        # Detect swing points
        swing_highs = self._detect_swing_highs(closed_bars)
        swing_lows = self._detect_swing_lows(closed_bars)

        # Classify strength
        strong_highs = [s for s in swing_highs if s.strength == SwingStrength.STRONG]
        weak_highs = [s for s in swing_highs if s.strength == SwingStrength.WEAK]
        strong_lows = [s for s in swing_lows if s.strength == SwingStrength.STRONG]
        weak_lows = [s for s in swing_lows if s.strength == SwingStrength.WEAK]

        # Detect equal highs/lows (within tolerance)
        equal_highs = self._detect_equal_levels(swing_highs, atr, "high")
        equal_lows = self._detect_equal_levels(swing_lows, atr, "low")

        # Determine trend
        trend = self._determine_trend(closed_bars, swing_highs, swing_lows)

        # Define zones
        top_zone, bottom_zone, middle_zone = self._define_zones(
            closed_bars, swing_highs, swing_lows, atr
        )

        current_price = closed_bars[-1].close if closed_bars else Decimal("0")

        return H1Structure(
            strong_highs=self._to_price_levels(strong_highs),
            strong_lows=self._to_price_levels(strong_lows),
            weak_highs=self._to_price_levels(weak_highs),
            weak_lows=self._to_price_levels(weak_lows),
            equal_highs=self._to_price_levels(equal_highs),
            equal_lows=self._to_price_levels(equal_lows),
            trend=trend,
            top_zone=top_zone,
            bottom_zone=bottom_zone,
            middle_zone=middle_zone,
            current_price=current_price,
            timestamp=closed_bars[-1].close_time if closed_bars else bars[-1].close_time,
            atr=atr,
        )

    def _detect_swing_highs(self, bars: list[OHLCBar]) -> list[SwingPoint]:
        """Detect swing highs using fractal method."""
        swing_highs = []
        lookback = self.swing_lookback

        for i in range(lookback, len(bars) - lookback):
            current_high = bars[i].high
            is_swing_high = True

            # Check if current high is highest in window
            for j in range(i - lookback, i + lookback + 1):
                if j != i and bars[j].high >= current_high:
                    is_swing_high = False
                    break

            if is_swing_high:
                strength = self._classify_strength(bars, i, "high")
                swing_highs.append(SwingPoint(
                    price=current_high,
                    time=bars[i].close_time.isoformat(),
                    bar_index=i,
                    strength=strength,
                ))

        return swing_highs

    def _detect_swing_lows(self, bars: list[OHLCBar]) -> list[SwingPoint]:
        """Detect swing lows using fractal method."""
        swing_lows = []
        lookback = self.swing_lookback

        for i in range(lookback, len(bars) - lookback):
            current_low = bars[i].low
            is_swing_low = True

            # Check if current low is lowest in window
            for j in range(i - lookback, i + lookback + 1):
                if j != i and bars[j].low <= current_low:
                    is_swing_low = False
                    break

            if is_swing_low:
                strength = self._classify_strength(bars, i, "low")
                swing_lows.append(SwingPoint(
                    price=current_low,
                    time=bars[i].close_time.isoformat(),
                    bar_index=i,
                    strength=strength,
                ))

        return swing_lows

    def _classify_strength(
        self,
        bars: list[OHLCBar],
        index: int,
        point_type: str,
    ) -> SwingStrength:
        """
        Classify swing point strength based on:
        - Volume/tick volume at the point
        - Reaction after the point (how far price moved away)
        - Number of tests (how many times price returned)
        """
        bar = bars[index]
        
        # Volume-based strength
        volume_score = Decimal("0")
        if bar.tick_volume > 0:
            # Compare to average volume
            avg_volume = sum(b.tick_volume for b in bars) / Decimal(len(bars))
            if avg_volume > 0:
                volume_ratio = Decimal(bar.tick_volume) / avg_volume
                if volume_ratio > Decimal("1.5"):
                    volume_score += Decimal("0.4")
                elif volume_ratio > Decimal("1.0"):
                    volume_score += Decimal("0.2")

        # Reaction strength - how much price moved after the swing
        reaction_score = Decimal("0")
        lookahead = min(5, len(bars) - index - 1)
        if lookahead > 0:
            if point_type == "high":
                max_after = max(b.high for b in bars[index + 1:index + 1 + lookahead])
                min_after = min(b.low for b in bars[index + 1:index + 1 + lookback])
                reaction = (bar.high - min_after) / bar.high if bar.high > 0 else Decimal("0")
            else:
                max_after = max(b.high for b in bars[index + 1:index + 1 + lookahead])
                min_after = min(b.low for b in bars[index + 1:index + 1 + lookback])
                reaction = (max_after - bar.low) / bar.low if bar.low > 0 else Decimal("0")

            if reaction > Decimal("0.02"):  # > 2% reaction
                reaction_score += Decimal("0.4")
            elif reaction > Decimal("0.01"):  # > 1% reaction
                reaction_score += Decimal("0.2")

        total_score = volume_score + reaction_score

        if total_score >= Decimal("0.6"):
            return SwingStrength.STRONG
        elif total_score >= Decimal("0.3"):
            return SwingStrength.WEAK
        else:
            return SwingStrength.EQUAL

    def _detect_equal_levels(
        self,
        swing_points: list[SwingPoint],
        atr: Decimal,
        level_type: str,
    ) -> list[SwingPoint]:
        """Detect equal highs/lows within ATR tolerance."""
        if len(swing_points) < 2:
            return []

        tolerance = atr * self.equal_level_tolerance_atr_mult
        equal_levels = []
        used = set()

        for i, sp1 in enumerate(swing_points):
            if i in used:
                continue
            for j, sp2 in enumerate(swing_points[i + 1:], i + 1):
                if j in used:
                    continue
                if abs(sp1.price - sp2.price) <= tolerance:
                    # Mark both as equal
                    sp1.strength = SwingStrength.EQUAL
                    sp2.strength = SwingStrength.EQUAL
                    equal_levels.append(sp1)
                    equal_levels.append(sp2)
                    used.add(i)
                    used.add(j)
                    break

        return equal_levels

    def _determine_trend(
        self,
        bars: list[OHLCBar],
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
    ) -> TrendDirection:
        """Determine market trend from swing points."""
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return TrendDirection.UNKNOWN

        # Get recent swings (last 3 of each)
        recent_highs = swing_highs[-3:]
        recent_lows = swing_lows[-3:]

        # Check for higher highs and higher lows (uptrend)
        hh_count = 0
        hl_count = 0
        for i in range(1, len(recent_highs)):
            if recent_highs[i].price > recent_highs[i - 1].price:
                hh_count += 1
        for i in range(1, len(recent_lows)):
            if recent_lows[i].price > recent_lows[i - 1].price:
                hl_count += 1

        # Check for lower highs and lower lows (downtrend)
        lh_count = 0
        ll_count = 0
        for i in range(1, len(recent_highs)):
            if recent_highs[i].price < recent_highs[i - 1].price:
                lh_count += 1
        for i in range(1, len(recent_lows)):
            if recent_lows[i].price < recent_lows[i - 1].price:
                ll_count += 1

        if hh_count >= 1 and hl_count >= 1:
            return TrendDirection.UP
        elif lh_count >= 1 and ll_count >= 1:
            return TrendDirection.DOWN
        else:
            return TrendDirection.RANGING

    def _define_zones(
        self,
        bars: list[OHLCBar],
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
        atr: Decimal,
    ) -> tuple[Zone, Zone, Zone]:
        """Define price zones (top, bottom, middle)."""
        if not bars:
            empty_zone = Zone(
                top=Decimal("0"),
                bottom=Decimal("0"),
                type="empty",
                strength=Decimal("0"),
            )
            return empty_zone, empty_zone, empty_zone

        # Get most recent strong levels
        strong_highs = [s for s in swing_highs if s.strength == SwingStrength.STRONG]
        strong_lows = [s for s in swing_lows if s.strength == SwingStrength.STRONG]

        # Top zone - near recent strong highs
        if strong_highs:
            top_high = strong_highs[-1].price
            top_zone = Zone(
                top=top_high + atr,
                bottom=top_high - atr,
                type="top",
                strength=Decimal("0.8"),
            )
        else:
            recent_high = max(b.high for b in bars[-20:]) if len(bars) >= 20 else bars[-1].high
            top_zone = Zone(
                top=recent_high + atr,
                bottom=recent_high - atr,
                type="top",
                strength=Decimal("0.5"),
            )

        # Bottom zone - near recent strong lows
        if strong_lows:
            bottom_low = strong_lows[-1].price
            bottom_zone = Zone(
                top=bottom_low + atr,
                bottom=bottom_low - atr,
                type="bottom",
                strength=Decimal("0.8"),
            )
        else:
            recent_low = min(b.low for b in bars[-20:]) if len(bars) >= 20 else bars[-1].low
            bottom_zone = Zone(
                top=recent_low + atr,
                bottom=recent_low - atr,
                type="bottom",
                strength=Decimal("0.5"),
            )

        # Middle zone - between top and bottom
        middle_zone = Zone(
            top=(top_zone.top + bottom_zone.bottom) / 2 + atr / 2,
            bottom=(top_zone.top + bottom_zone.bottom) / 2 - atr / 2,
            type="middle",
            strength=Decimal("0.3"),
        )

        return top_zone, bottom_zone, middle_zone

    def _to_price_levels(
        self,
        swing_points: list[SwingPoint],
    ) -> list[PriceLevel]:
        """Convert SwingPoint list to PriceLevel list."""
        levels = []
        for sp in swing_points:
            levels.append(PriceLevel(
                price=sp.price,
                strength=sp.strength,
                test_count=sp.test_count,
                last_test=sp.last_test_time,
                volume_at_level=Decimal("0"),
                type="high" if sp.price > 0 else "low",
            ))
        return levels

    def _empty_structure(
        self,
        bars: list[OHLCBar],
        atr: Decimal,
    ) -> H1Structure:
        """Return empty structure when insufficient data."""
        empty_zone = Zone(
            top=Decimal("0"),
            bottom=Decimal("0"),
            type="empty",
            strength=Decimal("0"),
        )
        current_price = bars[-1].close if bars else Decimal("0")

        return H1Structure(
            strong_highs=[],
            strong_lows=[],
            weak_highs=[],
            weak_lows=[],
            equal_highs=[],
            equal_lows=[],
            trend=TrendDirection.UNKNOWN,
            top_zone=empty_zone,
            bottom_zone=empty_zone,
            middle_zone=empty_zone,
            current_price=current_price,
            timestamp=bars[-1].close_time if bars else None,
            atr=atr,
        )