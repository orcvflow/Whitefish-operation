"""
Candle Pattern Classification

Formal definitions for candle patterns used in the strategy:
- 3-1: Strong bullish pattern (large body, small wicks)
- 1-1: Small body, long wick - bottom zone only with confirmation
- 1-3: Strong bearish pattern (large body, small wicks)
- 3-3: Long upper wick, body near bottom - top zone only with confirmation

All definitions are configurable and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..canonical import CandlePatternType, OHLCBar


@dataclass(frozen=True, slots=True)
class PatternConfig:
    """Configuration for candle pattern detection."""
    # 3-1 pattern: strong bullish
    three_one_min_body_ratio: Decimal = Decimal("0.6")  # body/range >= 0.6
    three_one_max_wick_ratio: Decimal = Decimal("0.2")   # each wick/range <= 0.2
    
    # 1-3 pattern: strong bearish
    one_three_min_body_ratio: Decimal = Decimal("0.6")
    one_three_max_wick_ratio: Decimal = Decimal("0.2")
    
    # 1-1 pattern: small body, long wick
    one_one_max_body_ratio: Decimal = Decimal("0.3")     # body/range <= 0.3
    one_one_min_wick_ratio: Decimal = Decimal("0.5")     # at least one wick/range >= 0.5
    
    # 3-3 pattern: long upper wick, body near bottom
    three_three_min_upper_wick_ratio: Decimal = Decimal("0.5")
    three_three_max_body_ratio: Decimal = Decimal("0.4")
    three_three_body_near_bottom: bool = True  # body in bottom 25% of range


class CandlePatternClassifier:
    """
    Classifies candle patterns with formal definitions.
    
    Patterns:
    - 3-1: Strong bullish - large body (>=60% of range), small wicks
    - 1-1: Indecision - small body (<=30% of range), long wick (>=50% of range)
    - 1-3: Strong bearish - large body (>=60% of range), small wicks
    - 3-3: Rejection - long upper wick (>=50% of range), body near bottom
    
    Zone requirements:
    - 3-1: Any zone (strong bullish momentum)
    - 1-1: Bottom zone only (bullish reversal signal)
    - 1-3: Any zone (strong bearish momentum)
    - 3-3: Top zone only (bearish reversal signal)
    
    Confirmation required:
    - 1-1: Requires previous bullish candle + bullish close
    - 3-3: Requires previous uptrend + bearish close
    """

    def __init__(self, config: Optional[PatternConfig] = None):
        self.config = config or PatternConfig()

    def classify(
        self,
        bar: OHLCBar,
        zone_type: Optional[str] = None,
        previous_bar: Optional[OHLCBar] = None,
        trend: Optional[str] = None,
    ) -> Optional[CandlePattern]:
        """
        Classify a candle pattern.
        
        Args:
            bar: The candle to classify
            zone_type: Current zone ("top", "bottom", "middle")
            previous_bar: Previous candle for confirmation
            trend: Current trend direction
            
        Returns:
            CandlePattern or None if no pattern matches
        """
        # Calculate ratios
        body_ratio = bar.body_ratio
        upper_wick_ratio = bar.upper_wick / bar.range_ if bar.range_ > 0 else Decimal("0")
        lower_wick_ratio = bar.lower_wick / bar.range_ if bar.range_ > 0 else Decimal("0")
        
        # Check 3-1: Strong bullish
        if self._is_three_one(body_ratio, upper_wick_ratio, lower_wick_ratio):
            return CandlePattern(
                pattern_type=CandlePatternType.THREE_ONE,
                bullish=True,
                body_ratio=body_ratio,
                upper_wick_ratio=upper_wick_ratio,
                lower_wick_ratio=lower_wick_ratio,
                confirmation_required=False,
                bar_index=0,  # Will be set by engine
            )
        
        # Check 1-3: Strong bearish
        if self._is_one_three(body_ratio, upper_wick_ratio, lower_wick_ratio):
            return CandlePattern(
                pattern_type=CandlePatternType.ONE_THREE,
                bullish=False,
                body_ratio=body_ratio,
                upper_wick_ratio=upper_wick_ratio,
                lower_wick_ratio=lower_wick_ratio,
                confirmation_required=False,
                bar_index=0,
            )
        
        # Check 1-1: Small body, long wick - bottom zone only
        if self._is_one_one(body_ratio, upper_wick_ratio, lower_wick_ratio):
            if zone_type == "bottom":
                # Requires confirmation: previous bullish or bullish close
                if self._confirm_one_one(bar, previous_bar):
                    return CandlePattern(
                        pattern_type=CandlePatternType.ONE_ONE,
                        bullish=True,
                        body_ratio=body_ratio,
                        upper_wick_ratio=upper_wick_ratio,
                        lower_wick_ratio=lower_wick_ratio,
                        confirmation_required=True,
                        bar_index=0,
                    )
        
        # Check 3-3: Long upper wick, body near bottom - top zone only
        if self._is_three_three(body_ratio, upper_wick_ratio, lower_wick_ratio):
            if zone_type == "top":
                # Requires confirmation: previous uptrend + bearish close
                if self._confirm_three_three(bar, previous_bar, trend):
                    return CandlePattern(
                        pattern_type=CandlePatternType.THREE_THREE,
                        bullish=False,
                        body_ratio=body_ratio,
                        upper_wick_ratio=upper_wick_ratio,
                        lower_wick_ratio=lower_wick_ratio,
                        confirmation_required=True,
                        bar_index=0,
                    )
        
        return None

    def _is_three_one(
        self,
        body_ratio: Decimal,
        upper_wick_ratio: Decimal,
        lower_wick_ratio: Decimal,
    ) -> bool:
        """Check 3-1 pattern: strong bullish."""
        return (
            body_ratio >= self.config.three_one_min_body_ratio
            and upper_wick_ratio <= self.config.three_one_max_wick_ratio
            and lower_wick_ratio <= self.config.three_one_max_wick_ratio
        )

    def _is_one_three(
        self,
        body_ratio: Decimal,
        upper_wick_ratio: Decimal,
        lower_wick_ratio: Decimal,
    ) -> bool:
        """Check 1-3 pattern: strong bearish."""
        return (
            body_ratio >= self.config.one_three_min_body_ratio
            and upper_wick_ratio <= self.config.one_three_max_wick_ratio
            and lower_wick_ratio <= self.config.one_three_max_wick_ratio
        )

    def _is_one_one(
        self,
        body_ratio: Decimal,
        upper_wick_ratio: Decimal,
        lower_wick_ratio: Decimal,
    ) -> bool:
        """Check 1-1 pattern: small body, long wick."""
        return (
            body_ratio <= self.config.one_one_max_body_ratio
            and (upper_wick_ratio >= self.config.one_one_min_wick_ratio
                 or lower_wick_ratio >= self.config.one_one_min_wick_ratio)
        )

    def _is_three_three(
        self,
        body_ratio: Decimal,
        upper_wick_ratio: Decimal,
        lower_wick_ratio: Decimal,
    ) -> bool:
        """Check 3-3 pattern: long upper wick, body near bottom."""
        body_in_bottom_25 = (
            lower_wick_ratio >= Decimal("0.75")
        )  # Body is in bottom 25% of range
        
        return (
            upper_wick_ratio >= self.config.three_three_min_upper_wick_ratio
            and body_ratio <= self.config.three_three_max_body_ratio
            and (not self.config.three_three_body_near_bottom or body_in_bottom_25)
        )

    def _confirm_one_one(
        self,
        bar: OHLCBar,
        previous_bar: Optional[OHLCBar],
    ) -> bool:
        """Confirm 1-1 pattern: previous bullish or current bullish close."""
        if previous_bar is None:
            return bar.is_bullish  # At least current bar should be bullish
        
        # Confirmation: previous bar was bullish OR current bar closed bullish
        return previous_bar.is_bullish or bar.is_bullish

    def _confirm_three_three(
        self,
        bar: OHLCBar,
        previous_bar: Optional[OHLCBar],
        trend: Optional[str],
    ) -> bool:
        """Confirm 3-3 pattern: previous uptrend + bearish close."""
        if not bar.is_bearish:
            return False  # Must be bearish close
        
        if previous_bar is None:
            return trend == "up"  # Need uptrend context
        
        # Confirmation: previous bar was bullish (uptrend) and current is bearish
        return previous_bar.is_bullish and bar.is_bearish


# Default pattern config instance
DEFAULT_PATTERN_CONFIG = PatternConfig()