"""
Decision Engine

Deterministic decision engine - produces OPEN/WAIT/SKIP.
Combines fundamental + technical + risk analysis.
AI cannot override hard risk gates.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from ..canonical import (
    Instrument,
    QualityStatus,
    DecisionStatus,
    VerificationStatus,
    GateType,
    Decision,
    FundamentalThesis,
    TechnicalAnalysis,
    MarketState,
    RiskCheckResult,
    RiskConfig,
)
from ..risk.engine import RiskEngine, RiskContext
from ..axle.verifier import AXLEVerifier

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Deterministic decision engine.
    
    Decision logic:
    1. If any hard risk gate failed → SKIP
    2. If fundamental + technical align + valid setup → OPEN
    3. If setup forming but trigger not hit → WAIT
    4. If conflict (fund bullish + tech bearish) → SKIP
    
    OPEN is NOT a broker order - it's a trade plan ready for execution.
    """

    def __init__(
        self,
        risk_config: RiskConfig,
        axle_verifier: Optional[AXLEVerifier] = None,
        strategy_version: str = "1.0.0",
    ):
        self.risk_config = risk_config
        self.risk_engine = RiskEngine(risk_config)
        self.axle_verifier = axle_verifier
        self.strategy_version = strategy_version
        self._decision_count = 0
        self._open_count = 0
        self._skip_count = 0
        self._wait_count = 0

    def decide(
        self,
        symbol: Instrument,
        snapshot_id: uuid.UUID,
        technical: TechnicalAnalysis,
        fundamental: FundamentalThesis,
        market_state: MarketState,
        calendar_ids: list[uuid.UUID],
        news_ids: list[uuid.UUID],
        account_equity: Decimal,
        daily_pnl: Decimal,
        trades_today: int,
    ) -> Decision:
        """
        Make trading decision.
        
        Args:
            symbol: Trading instrument
            snapshot_id: Analysis snapshot ID
            technical: Technical analysis results
            fundamental: Fundamental analysis results
            market_state: Current market state
            calendar_ids: Related calendar event IDs
            news_ids: Related news item IDs
            account_equity: Account equity for position sizing
            daily_pnl: Today's P&L
            trades_today: Number of trades today
            
        Returns:
            Decision with OPEN/WAIT/SKIP status and full audit trail
        """
        self._decision_count += 1
        now = datetime.now(timezone.utc)
        decision_id = uuid.uuid4()

        # Build risk context
        risk_ctx = RiskContext(
            symbol=symbol,
            account_equity=account_equity,
            daily_pnl=daily_pnl,
            trades_today=trades_today,
            technical=technical,
            fundamental=fundamental,
            market_state=market_state,
            config=self.risk_config,
        )

        # Run risk checks
        risk_result = self.risk_engine.check_pre_trade(risk_ctx)
        failed_gates = []
        if not risk_result.passed:
            # Collect all failed gates from individual checks
            failed_gates = self._get_failed_gates(risk_ctx)

        # AXLE verification (non-blocking)
        axle_results = {}
        if self.axle_verifier:
            axle_results = self._run_axle_verification(technical)

        # Determine decision
        status = self._determine_status(
            risk_result=risk_result,
            failed_gates=failed_gates,
            technical=technical,
            fundamental=fundamental,
            axle_results=axle_results,
        )

        # Calculate entry/SL/TP if OPEN
        entry = technical.entry_trigger
        sl = technical.sl
        tp1 = technical.tp1
        tp2 = technical.tp2
        rr = technical.rr
        risk_pct = None
        position_size = None

        if status == DecisionStatus.OPEN and entry and sl:
            # Calculate risk percentage
            risk_pct = self._calculate_risk_pct(
                entry=entry,
                sl=sl,
                account_equity=account_equity,
            )
            # Cap at max trade risk
            if risk_pct and risk_pct > self.risk_config.max_trade_risk_pct:
                risk_pct = self.risk_config.max_trade_risk_pct

            # Calculate position size
            instrument_spec = self._get_instrument_spec(symbol)
            position_size = self.risk_engine.check_position_size(
                account_equity=account_equity,
                risk_pct=risk_pct or self.risk_config.default_risk_pct,
                entry=entry,
                sl=sl,
                contract_size=instrument_spec.contract_size if instrument_spec else Decimal("1.0"),
                tick_value=instrument_spec.tick_value if instrument_spec else Decimal("1.0"),
            )

        # Update counters
        if status == DecisionStatus.OPEN:
            self._open_count += 1
        elif status == DecisionStatus.SKIP:
            self._skip_count += 1
        else:
            self._wait_count += 1

        # Build decision
        decision = Decision(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            symbol=symbol,
            status=status,
            timestamp=now,
            news_ids=news_ids,
            calendar_ids=calendar_ids,
            entry=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            risk_pct=risk_pct,
            rr=rr,
            failed_gates=failed_gates,
            strategy_version=self.strategy_version,
            data_quality=technical.data_quality,
            axle_verification=axle_results,
            fundamental_thesis_id=None,  # Will be set by caller
            technical_analysis_id=None,  # Will be set by caller
            position_size=position_size,
            notes=self._build_notes(status, failed_gates, technical, fundamental),
        )

        logger.info(
            f"Decision: {status.value} for {symbol.value} | "
            f"failed_gates={[g.value for g in failed_gates]} | "
            f"entry={entry} sl={sl} tp1={tp1} tp2={tp2} rr={rr}"
        )

        return decision

    def _get_failed_gates(self, ctx: RiskContext) -> list[GateType]:
        """Get all failed risk gates."""
        failed = []

        # Spread
        spread_check = self.risk_engine._check_spread(ctx)
        if not spread_check.passed:
            failed.append(GateType.SPREAD)

        # Data quality
        quality_check = self.risk_engine._check_data_quality(ctx)
        if not quality_check.passed:
            failed.append(GateType.DATA_QUALITY)

        # Market hours
        hours_check = self.risk_engine._check_market_hours(ctx)
        if not hours_check.passed:
            failed.append(GateType.MARKET_HOURS)

        # Blackout
        blackout_check = self.risk_engine._check_blackout(ctx)
        if not blackout_check.passed:
            failed.append(GateType.BLACKOUT)

        # Risk/Reward
        rr_check = self.risk_engine._check_risk_reward(ctx)
        if not rr_check.passed:
            failed.append(GateType.RISK_REWARD)

        # Daily limits
        daily_check = self.risk_engine._check_daily_limits(ctx)
        if not daily_check.passed:
            failed.append(GateType.DAILY_LIMITS)

        # Stale data
        stale_check = self.risk_engine._check_stale_data(ctx)
        if not stale_check.passed:
            failed.append(GateType.STALE_DATA)

        return failed

    def _determine_status(
        self,
        risk_result: RiskCheckResult,
        failed_gates: list[GateType],
        technical: TechnicalAnalysis,
        fundamental: FundamentalThesis,
        axle_results: dict,
    ) -> DecisionStatus:
        """Determine decision status from all inputs."""

        # Rule 1: Any hard gate failed → SKIP
        if failed_gates:
            logger.debug(f"SKIP: failed gates={[g.value for g in failed_gates]}")
            return DecisionStatus.SKIP

        # Rule 2: AXLE verification failed → SKIP (if verification was attempted)
        for check_name, status in axle_results.items():
            if status == VerificationStatus.FAILED:
                logger.debug(f"SKIP: AXLE verification failed: {check_name}")
                return DecisionStatus.SKIP

        # Rule 3: Check for fundamental-technical conflict
        conflict = self._check_conflict(fundamental, technical)
        if conflict:
            logger.debug(f"SKIP: fundamental-technical conflict: {conflict}")
            return DecisionStatus.SKIP

        # Rule 4: Check if setup is valid (trigger hit)
        if technical.entry_trigger is not None and technical.sl is not None:
            # Check if RSI condition is met
            rsi_ok = self._check_rsi_condition(technical)
            if rsi_ok:
                logger.debug("OPEN: valid setup with trigger")
                return DecisionStatus.OPEN
            else:
                logger.debug("WAIT: setup forming but RSI condition not met")
                return DecisionStatus.WAIT

        # Rule 5: No valid setup → WAIT
        logger.debug("WAIT: no valid setup")
        return DecisionStatus.WAIT

    def _check_conflict(
        self,
        fundamental: FundamentalThesis,
        technical: TechnicalAnalysis,
    ) -> Optional[str]:
        """Check for fundamental-technical conflict."""
        if not fundamental.per_instrument_impact:
            return None

        # Get impact for this symbol
        symbol_impact = None
        for imp in fundamental.per_instrument_impact:
            if imp.symbol == technical.symbol:
                symbol_impact = imp
                break

        if not symbol_impact:
            return None

        # Check conflict: fund bullish + tech bearish OR vice versa
        if symbol_impact.direction == "bullish" and technical.pattern:
            if technical.pattern.pattern_type.name == "ONE_THREE" or technical.pattern.pattern_type.name == "THREE_THREE":
                if technical.entry_direction == "SELL":
                    return "Fundamental bullish but technical bearish pattern"

        if symbol_impact.direction == "bearish" and technical.pattern:
            if technical.pattern.pattern_type.name == "THREE_ONE" or technical.pattern.pattern_type.name == "ONE_ONE":
                if technical.entry_direction == "BUY":
                    return "Fundamental bearish but technical bullish pattern"

        return None

    def _check_rsi_condition(self, technical: TechnicalAnalysis) -> bool:
        """Check if RSI condition is met for entry."""
        rsi = technical.m15_rsi

        if technical.entry_direction == "BUY":
            # RSI 20-30: BUY candidate only in bottom zone
            return Decimal("20") <= rsi <= Decimal("30")
        elif technical.entry_direction == "SELL":
            # RSI 70-80: SELL candidate only in top zone
            return Decimal("70") <= rsi <= Decimal("80")

        return False

    def _calculate_risk_pct(
        self,
        entry: Decimal,
        sl: Decimal,
        account_equity: Decimal,
    ) -> Optional[Decimal]:
        """Calculate risk percentage for the trade."""
        if account_equity <= 0:
            return None

        stop_distance = abs(entry - sl)
        if stop_distance == 0:
            return None

        # Simplified: risk_pct = stop_distance / entry (approximate)
        # Actual risk depends on position size
        risk_pct = stop_distance / entry
        return risk_pct.quantize(Decimal("0.0001"))

    def _get_instrument_spec(self, symbol: Instrument):
        """Get instrument specification."""
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

    def _build_notes(
        self,
        status: DecisionStatus,
        failed_gates: list[GateType],
        technical: TechnicalAnalysis,
        fundamental: FundamentalThesis,
    ) -> str:
        """Build human-readable decision notes."""
        notes = []

        if status == DecisionStatus.SKIP:
            if failed_gates:
                notes.append(f"SKIP - Failed gates: {[g.value for g in failed_gates]}")
            else:
                notes.append("SKIP - No valid setup")

        if technical.pattern:
            notes.append(f"Pattern: {technical.pattern.pattern_type.value}")
        if technical.m15_rsi:
            notes.append(f"RSI(14): {technical.m15_rsi}")
        if technical.rr:
            notes.append(f"RR: {technical.rr}")

        if fundamental.uncertainty_assessment:
            notes.append(f"Uncertainty: {fundamental.uncertainty_assessment}")

        return " | ".join(notes) if notes else ""

    @property
    def stats(self) -> dict:
        """Get engine statistics."""
        return {
            "decision_count": self._decision_count,
            "open_count": self._open_count,
            "skip_count": self._skip_count,
            "wait_count": self._wait_count,
        }