"""
Technical Analysis Engine

Deterministic technical analysis - no AI involvement.
H1 structure, M15 RSI, candle patterns, entry/SL/TP, risk checks.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from ..canonical import (
    Instrument,
    Timeframe,
    QualityStatus,
    OHLCBar,
    H1Structure,
    CandlePattern,
    TechnicalAnalysis,
    TechnicalConfig,
    DecisionStatus,
    GateType,
)
from .indicators import rsi, atr
from .structure import H1StructureAnalyzer
from .patterns import CandlePatternClassifier, DEFAULT_PATTERN_CONFIG
from .levels import (
    calculate_entry,
    calculate_sl,
    calculate_tp,
    calculate_risk_reward,
)

logger = logging.getLogger(__name__)


class TechnicalEngine:
    """
    Deterministic technical analysis engine.
    
    All calculations are pure Python - no AI, no external models.
    Uses only closed bars to avoid look-ahead bias.
    """

    def __init__(self, config: Optional[TechnicalConfig] = None):
        self.config = config or TechnicalConfig()
        self.structure_analyzer = H1StructureAnalyzer(
            swing_lookback=self.config.swing_lookback,
            equal_level_tolerance_atr_mult=self.config.equal_level_tolerance_atr_mult,
        )
        self.pattern_classifier = CandlePatternClassifier(DEFAULT_PATTERN_CONFIG)
        self._analysis_count = 0

    def analyze(
        self,
        symbol: Instrument,
        ohlc_h1: list[OHLCBar],
        ohlc_m15: list[OHLCBar],
    ) -> TechnicalAnalysis:
        """
        Run complete technical analysis.
        
        Args:
            symbol: Trading instrument
            ohlc_h1: H1 OHLC bars (closed only, oldest first)
            ohlc_m15: M15 OHLC bars (closed only, oldest first)
            
        Returns:
            TechnicalAnalysis with all technical data
        """
        self._analysis_count += 1

        # Use only closed bars
        closed_h1 = [b for b in ohlc_h1 if not b.is_open]
        closed_m15 = [b for b in ohlc_m15 if not b.is_open]

        if not closed_h1:
            closed_h1 = ohlc_h1
        if not closed_m15:
            closed_m15 = ohlc_m15

        # 1. H1 Market Structure
        h1_closes = [b.close for b in closed_h1]
        h1_highs = [b.high for b in closed_h1]
        h1_lows = [b.low for b in closed_h1]

        h1_atr = atr(h1_highs, h1_lows, h1_closes, period=14) or Decimal("0")

        h1_structure = self.structure_analyzer.analyze(closed_h1, h1_atr)

        # 2. M15 RSI(14)
        m15_closes = [b.close for b in closed_m15]
        m15_highs = [b.high for b in closed_m15]
        m15_lows = [b.low for b in closed_m15]

        m15_rsi_value = rsi(m15_closes, period=self.config.rsi_period) or Decimal("0")
        m15_atr_value = atr(m15_highs, m15_lows, m15_closes, period=14) or Decimal("0")

        # 3. Last closed M15 bar
        last_m15_bar = closed_m15[-1] if closed_m15 else None

        # 4. Candle pattern classification
        pattern = None
        if last_m15_bar and len(closed_m15) >= 2:
            prev_bar = closed_m15[-2]

            # Determine which zone the price is in
            zone_type = self._get_zone_type(last_m15_bar.close, h1_structure)

            # Classify pattern
            pattern = self.pattern_classifier.classify(
                bar=last_m15_bar,
                zone_type=zone_type,
                previous_bar=prev_bar,
                trend=h1_structure.trend.value,
            )

        # 5. Calculate entry/SL/TP
        entry_trigger = None
        entry_direction = None
        sl = None
        tp1 = None
        tp2 = None
        rr = None

        if pattern:
            # Determine direction from pattern
            if pattern.bullish:
                entry_direction = "BUY"
                signal_high = last_m15_bar.high if last_m15_bar else Decimal("0")
                instrument_spec = self._get_instrument_spec(symbol)
                entry_trigger = calculate_entry(
                    signal_high=signal_high,
                    signal_low=last_m15_bar.low,
                    direction="BUY",
                    point_value=instrument_spec.point_value if instrument_spec else Decimal("0.00001"),
                    buffer_points=self.config.entry_buffer_points,
                )
            else:
                entry_direction = "SELL"
                signal_low = last_m15_bar.low if last_m15_bar else Decimal("0")
                instrument_spec = self._get_instrument_spec(symbol)
                entry_trigger = calculate_entry(
                    signal_high=last_m15_bar.high,
                    signal_low=signal_low,
                    direction="SELL",
                    point_value=instrument_spec.point_value if instrument_spec else Decimal("0.00001"),
                    buffer_points=self.config.entry_buffer_points,
                )

            # Calculate SL
            if last_m15_bar:
                instrument_spec = self._get_instrument_spec(symbol)
                liquidity_levels = [
                    l.price for l in h1_structure.equal_highs + h1_structure.equal_lows
                ]
                sl = calculate_sl(
                    signal_candle=last_m15_bar,
                    direction=entry_direction,
                    buffer_points=self.config.sl_buffer_points,
                    point_value=instrument_spec.point_value if instrument_spec else Decimal("0.00001"),
                    liquidity_levels=liquidity_levels,
                )

            # Calculate TP1, TP2
            if entry_trigger and sl:
                tp1, tp2 = calculate_tp(
                    entry=entry_trigger,
                    sl=sl,
                    direction=entry_direction,
                    structure=h1_structure,
                    risk_reward=Decimal("2.0"),
                )
                rr = calculate_risk_reward(
                    entry=entry_trigger,
                    sl=sl,
                    tp1=tp1,
                    direction=entry_direction,
                )

        # 6. Risk checks
        risk_checks = self._run_risk_checks(
            symbol=symbol,
            spread=h1_structure.current_price and Decimal("0"),  # Will be set by engine
            rsi=m15_rsi_value,
            data_quality=QualityStatus.CLEAN,
        )

        current_price = closed_h1[-1].close if closed_h1 else Decimal("0")

        return TechnicalAnalysis(
            symbol=symbol,
            timestamp=closed_h1[-1].close_time if closed_h1 else None,
            h1_structure=h1_structure,
            m15_rsi=m15_rsi_value,
            m15_atr=m15_atr_value,
            last_closed_m15_bar=last_m15_bar or closed_h1[-1],
            pattern=pattern,
            entry_trigger=entry_trigger,
            entry_direction=entry_direction,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            rr=rr,
            spread=Decimal("0"),  # Will be updated by decision engine
            risk_checks=risk_checks,
            data_quality=QualityStatus.CLEAN,
        )

    def _get_zone_type(
        self,
        price: Decimal,
        structure: H1Structure,
    ) -> Optional[str]:
        """Determine which zone price is in."""
        if structure.top_zone.bottom <= price <= structure.top_zone.top:
            return "top"
        elif structure.bottom_zone.bottom <= price <= structure.bottom_zone.top:
            return "bottom"
        elif structure.middle_zone.bottom <= price <= structure.middle_zone.top:
            return "middle"
        return None

    def _get_instrument_spec(self, symbol: Instrument):
        """Get instrument specification (placeholder - should come from config)."""
        # This should be injected from app config
        from ..canonical import InstrumentSpec
        specs = {
            Instrument.XAUUSD: InstrumentSpec(
                symbol=Instrument.XAUUSD,
                provider_symbol="XAUUSD",
                description="Gold vs USD",
                precision=2,
                point_value=Decimal("0.01"),
                contract_size=Decimal("1.0"),
                tick_value=Decimal("1.0"),
                trading_hours={"open": "22:00", "close": "21:00"},
            ),
            Instrument.EURUSD: InstrumentSpec(
                symbol=Instrument.EURUSD,
                provider_symbol="EURUSD",
                description="Euro vs USD",
                precision=5,
                point_value=Decimal("0.00001"),
                contract_size=Decimal("1.0"),
                tick_value=Decimal("1.0"),
                trading_hours={"open": "22:00", "close": "21:00"},
            ),
            Instrument.GBPUSD: InstrumentSpec(
                symbol=Instrument.GBPUSD,
                provider_symbol="GBPUSD",
                description="Pound vs USD",
                precision=5,
                point_value=Decimal("0.00001"),
                contract_size=Decimal("1.0"),
                tick_value=Decimal("1.0"),
                trading_hours={"open": "22:00", "close": "21:00"},
            ),
            Instrument.WTI: InstrumentSpec(
                symbol=Instrument.WTI,
                provider_symbol="WTI",
                description="WTI Crude Oil",
                precision=2,
                point_value=Decimal("0.01"),
                contract_size=Decimal("1.0"),
                tick_value=Decimal("1.0"),
                trading_hours={"open": "22:00", "close": "21:00"},
            ),
        }
        return specs.get(symbol)

    def _run_risk_checks(
        self,
        symbol: Instrument,
        spread: Decimal,
        rsi: Decimal,
        data_quality: QualityStatus,
    ) -> dict:
        """Run basic technical risk checks."""
        return {
            GateType.SPREAD.value: spread < Decimal("0.001"),  # Will be overridden by risk engine
            GateType.DATA_QUALITY.value: data_quality == QualityStatus.CLEAN,
            GateType.STALE_DATA.value: True,  # Will be overridden
        }

    @property
    def stats(self) -> dict:
        """Get engine statistics."""
        return {
            "analysis_count": self._analysis_count,
        }