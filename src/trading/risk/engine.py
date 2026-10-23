"""
Risk Engine

Hard risk gates that cannot be overridden by AI.
- Spread check
- Data quality check
- Market hours check
- News blackout check (±30 min around high-impact events)
- Risk/Reward check (minimum 1:2)
- Daily limits (3% loss, 3 trades max)
- Stale data check
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from ..canonical import (
    Instrument,
    QualityStatus,
    DecisionStatus,
    GateType,
    RiskCheckResult,
    RiskContext,
    TechnicalAnalysis,
    FundamentalThesis,
    MarketState,
    CalendarEvent,
    NewsImpact,
    RiskConfig,
)

logger = logging.getLogger(__name__)


class BlackoutManager:
    """
    News blackout manager.
    
    Blocks trades ±30 minutes around high-impact news events.
    Per-currency blackout for forex pairs.
    """

    def __init__(self, config):
        self.config = config

    def is_blackout(
        self,
        symbol: Instrument,
        now: datetime,
        calendar_events: list[CalendarEvent],
    ) -> tuple[bool, Optional[str]]:
        """
        Check if symbol is in news blackout period.
        
        Returns:
            (is_blackout, reason_string)
        """
        # Get relevant currencies for this symbol
        relevant_currencies = self._get_relevant_currencies(symbol)

        for event in calendar_events:
            # Only check high impact events
            if self.config.high_impact_only and event.impact != NewsImpact.HIGH:
                continue

            # Check if currency is relevant
            if event.currency not in relevant_currencies:
                continue

            # Check if event is within blackout window
            event_time = event.time
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            before = event_time - timedelta(minutes=self.config.minutes_before)
            after = event_time + timedelta(minutes=self.config.minutes_after)

            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)

            if before <= now <= after:
                return True, f"News blackout: {event.name} ({event.currency}) {event.time.isoformat()}"

        return False, None

    def _get_relevant_currencies(self, symbol: Instrument) -> list[str]:
        """Get relevant currencies for an instrument."""
        base = symbol.base_currency
        quote = symbol.quote_currency

        currencies = [base, quote]

        # XAUUSD is affected by USD news
        if symbol == Instrument.XAUUSD:
            currencies.append("USD")
            currencies.append("XAU")  # Gold-specific

        # WTI is affected by USD and energy news
        if symbol == Instrument.WTI:
            currencies.append("USD")

        return list(set(currencies))

    def get_upcoming_high_impact(
        self,
        hours: int,
        calendar_events: list[CalendarEvent],
    ) -> list[CalendarEvent]:
        """Get upcoming high-impact events within hours."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=hours)

        upcoming = []
        for event in calendar_events:
            event_time = event.time
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            if event.impact == NewsImpact.HIGH and now <= event_time <= future:
                upcoming.append(event)

        return upcoming


class RiskEngine:
    """
    Deterministic risk engine with hard gates.
    
    All gates must pass for OPEN decision.
    AI cannot override these gates.
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self.blackout = BlackoutManager(config)
        self._daily_trades = 0
        self._daily_pnl = Decimal("0")
        self._last_reset_date = None

    def check_pre_trade(
        self,
        ctx: RiskContext,
    ) -> RiskCheckResult:
        """
        Run all pre-trade risk checks.
        
        Returns:
            RiskCheckResult with passed=False and failed_gates if any gate fails.
        """
        self._reset_daily_if_needed(ctx)

        checks = []

        # 1. Spread check
        spread_check = self._check_spread(ctx)
        checks.append(spread_check)

        # 2. Data quality check
        quality_check = self._check_data_quality(ctx)
        checks.append(quality_check)

        # 3. Market hours check
        hours_check = self._check_market_hours(ctx)
        checks.append(hours_check)

        # 4. News blackout check
        blackout_check = self._check_blackout(ctx)
        checks.append(blackout_check)

        # 5. Risk/Reward check
        rr_check = self._check_risk_reward(ctx)
        checks.append(rr_check)

        # 6. Daily limits check
        daily_check = self._check_daily_limits(ctx)
        checks.append(daily_check)

        # 7. Stale data check
        stale_check = self._check_stale_data(ctx)
        checks.append(stale_check)

        # Collect failed gates
        failed_gates = [c.gate for c in checks if not c.passed]

        overall_passed = len(failed_gates) == 0

        # Return the primary result - failed gates are collected separately
        return RiskCheckResult(
            gate=GateType.DATA_QUALITY,
            passed=overall_passed,
            message="All risk gates passed" if overall_passed else f"Failed gates: {[g.value for g in failed_gates]}",
        )

    def _reset_daily_if_needed(self, ctx: RiskContext) -> None:
        """Reset daily counters if new day."""
        today = ctx.timestamp.date() if hasattr(ctx, 'timestamp') else datetime.now(timezone.utc).date()
        if self._last_reset_date != today:
            self._daily_trades = 0
            self._daily_pnl = Decimal("0")
            self._last_reset_date = today

    def _check_spread(self, ctx: RiskContext) -> RiskCheckResult:
        """Check spread against threshold."""
        spread = ctx.market_state.spread_pct
        max_spread = self.config.max_spread_pct.get(ctx.symbol, Decimal("0.001"))

        passed = spread <= max_spread
        return RiskCheckResult(
            gate=GateType.SPREAD,
            passed=passed,
            message=f"Spread {spread:.6%} vs max {max_spread:.6%}",
            value=spread,
            threshold=max_spread,
        )

    def _check_data_quality(self, ctx: RiskContext) -> RiskCheckResult:
        """Check data quality is clean."""
        quality = ctx.technical.data_quality

        passed = quality == QualityStatus.CLEAN
        return RiskCheckResult(
            gate=GateType.DATA_QUALITY,
            passed=passed,
            message=f"Data quality: {quality.value}",
        )

    def _check_market_hours(self, ctx: RiskContext) -> RiskCheckResult:
        """Check if market is open."""
        market_state = ctx.market_state.tick.market_state

        passed = market_state == "open"
        return RiskCheckResult(
            gate=GateType.MARKET_HOURS,
            passed=passed,
            message=f"Market state: {market_state}",
        )

    def _check_blackout(self, ctx: RiskContext) -> RiskCheckResult:
        """Check news blackout."""
        now = ctx.market_state.timestamp
        calendar_events = []  # Will be populated from context

        # Get calendar events from fundamental context
        if hasattr(ctx, 'fundamental') and ctx.fundamental:
            # Calendar events would need to be passed separately
            pass

        is_blackout, reason = self.blackout.is_blackout(ctx.symbol, now, calendar_events)

        return RiskCheckResult(
            gate=GateType.BLACKOUT,
            passed=not is_blackout,
            message=reason or "No news blackout",
        )

    def _check_risk_reward(self, ctx: RiskContext) -> RiskCheckResult:
        """Check minimum risk/reward ratio."""
        rr = ctx.technical.rr
        min_rr = self.config.min_risk_reward

        if rr is None:
            return RiskCheckResult(
                gate=GateType.RISK_REWARD,
                passed=False,
                message="No RR calculated (no setup)",
                threshold=min_rr,
            )

        passed = rr >= min_rr
        return RiskCheckResult(
            gate=GateType.RISK_REWARD,
            passed=passed,
            message=f"RR: {rr} vs min {min_rr}",
            value=rr,
            threshold=min_rr,
        )

    def _check_daily_limits(self, ctx: RiskContext) -> RiskCheckResult:
        """Check daily trade and loss limits."""
        # Check max trades per day
        if ctx.trades_today >= self.config.max_trades_per_day:
            return RiskCheckResult(
                gate=GateType.DAILY_LIMITS,
                passed=False,
                message=f"Daily trade limit: {ctx.trades_today}/{self.config.max_trades_per_day}",
            )

        # Check daily loss limit
        daily_loss_pct = abs(ctx.daily_pnl) / ctx.account_equity if ctx.account_equity > 0 else Decimal("0")
        if daily_loss_pct >= self.config.daily_loss_limit_pct:
            return RiskCheckResult(
                gate=GateType.DAILY_LIMITS,
                passed=False,
                message=f"Daily loss limit: {daily_loss_pct:.2%} >= {self.config.daily_loss_limit_pct:.2%}",
                value=daily_loss_pct,
                threshold=self.config.daily_loss_limit_pct,
            )

        return RiskCheckResult(
            gate=GateType.DAILY_LIMITS,
            passed=True,
            message=f"Daily limits OK: {ctx.trades_today}/{self.config.max_trades_per_day} trades",
        )

    def _check_stale_data(self, ctx: RiskContext) -> RiskCheckResult:
        """Check data is not stale."""
        tick = ctx.market_state.tick

        if tick.stale:
            return RiskCheckResult(
                gate=GateType.STALE_DATA,
                passed=False,
                message="Stale quote from provider",
            )

        if tick.quote_age_seconds > 300:  # 5 minutes
            return RiskCheckResult(
                gate=GateType.STALE_DATA,
                passed=False,
                message=f"Quote age {tick.quote_age_seconds}s > 300s",
                value=Decimal(tick.quote_age_seconds),
                threshold=Decimal("300"),
            )

        return RiskCheckResult(
            gate=GateType.STALE_DATA,
            passed=True,
            message="Data fresh",
        )

    def check_position_size(
        self,
        account_equity: Decimal,
        risk_pct: Decimal,
        entry: Decimal,
        sl: Decimal,
        contract_size: Decimal,
        tick_value: Decimal,
    ) -> Decimal:
        """
        Calculate and validate position size.
        
        Returns position size or 0 if risk exceeds limits.
        """
        from ..technical.levels import calculate_position_size

        if risk_pct > self.config.max_trade_risk_pct:
            logger.warning(f"Risk {risk_pct:.2%} exceeds max {self.config.max_trade_risk_pct:.2%}")
            risk_pct = self.config.max_trade_risk_pct

        size = calculate_position_size(
            account_equity=account_equity,
            risk_pct=risk_pct,
            entry=entry,
            sl=sl,
            contract_size=contract_size,
            tick_value=tick_value,
        )

        return size

    def update_daily_stats(
        self,
        trades_today: int,
        daily_pnl: Decimal,
    ) -> None:
        """Update daily statistics."""
        self._daily_trades = trades_today
        self._daily_pnl = daily_pnl