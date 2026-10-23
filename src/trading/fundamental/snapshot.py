"""
Fundamental Snapshot Builder

Creates compact, immutable context snapshots for AI analysis.
Contains news, calendar, market state, and technical data.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from ..canonical import (
    Instrument,
    CanonicalTick,
    NewsItem,
    CalendarEvent,
    TechnicalAnalysis,
    FundamentalSnapshot,
    MarketState,
    QualityStatus,
)


class FundamentalSnapshotBuilder:
    """
    Builds fundamental analysis snapshots from raw data.
    
    Snapshots are immutable - new data creates new snapshot, never modifies old.
    Each snapshot gets a unique analysis_snapshot_id for audit trail.
    """

    def __init__(self, strategy_version: str = "1.0.0"):
        self.strategy_version = strategy_version

    def build(
        self,
        symbol: Instrument,
        tick: CanonicalTick,
        news: list[NewsItem],
        calendar: list[CalendarEvent],
        technical: Optional[TechnicalAnalysis] = None,
        quality: QualityStatus = QualityStatus.CLEAN,
    ) -> FundamentalSnapshot:
        """
        Build immutable fundamental snapshot.
        
        Args:
            symbol: Trading instrument
            tick: Latest canonical tick
            news: Relevant news items
            calendar: Relevant calendar events
            technical: Optional technical analysis
            quality: Data quality status
            
        Returns:
            Immutable FundamentalSnapshot
        """
        snapshot_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Sort news by published_at (newest first)
        sorted_news = sorted(news, key=lambda n: n.published_at, reverse=True)

        # Sort calendar by time (soonest first)
        sorted_calendar = sorted(calendar, key=lambda c: c.time)

        # Build market state
        market_state = MarketState(
            symbol=symbol,
            timestamp=now,
            tick=tick,
            spread_pct=tick.spread / tick.mid if tick.mid > 0 else Decimal("0"),
            session=self._get_market_session(now),
        )

        return FundamentalSnapshot(
            snapshot_id=snapshot_id,
            timestamp=now,
            symbol=symbol,
            tick_ref=tick,
            news_refs=sorted_news,
            calendar_refs=sorted_calendar,
            technical_ref=technical,
            quality=quality,
        )

    def _get_market_session(self, ts: datetime) -> str:
        """Determine market session from UTC timestamp."""
        hour = ts.hour

        # Approximate sessions (UTC)
        if 22 <= hour or hour < 2:
            return "asian"
        elif 2 <= hour < 12:
            return "london"
        elif 12 <= hour < 22:
            return "ny"
        else:
            return "overlap"

    def build_market_state(
        self,
        symbol: Instrument,
        tick: CanonicalTick,
    ) -> MarketState:
        """Build market state from tick."""
        now = datetime.now(timezone.utc)
        return MarketState(
            symbol=symbol,
            timestamp=now,
            tick=tick,
            spread_pct=tick.spread / tick.mid if tick.mid > 0 else Decimal("0"),
            session=self._get_market_session(now),
        )


class FundamentalPromptBuilder:
    """
    Builds structured prompts for fundamental AI analysis.
    
    Prompt requires AI to output structured JSON with:
    - thesis, sources, causal_chain, per_instrument_impact
    - counter_arguments, invalidation_conditions, uncertainty_assessment
    """

    SYSTEM_PROMPT = """You are a senior fundamental analyst for financial markets.
Analyze news and economic calendar events to produce a structured fundamental thesis.

OUTPUT FORMAT: Return valid JSON only, no markdown code blocks.

Required JSON structure:
{
  "thesis": "Clear summary of the fundamental view",
  "sources": ["URL1", "URL2"],
  "causal_chain": [
    {"step": 1, "description": "Event description", "evidence": "Source reference", "confidence": "proven|likely|speculative"}
  ],
  "per_instrument_impact": [
    {
      "symbol": "XAUUSD|EURUSD|GBPUSD|WTI",
      "direction": "bullish|bearish|neutral|mixed",
      "magnitude": "strong|moderate|weak",
      "reasoning": "Why this instrument is affected",
      "key_levels": [123.45, 678.90]
    }
  ],
  "counter_arguments": ["Why the thesis might be wrong"],
  "invalidation_conditions": ["What would invalidate this thesis"],
  "uncertainty_assessment": "High|Moderate|Low - explain why"
}

ANALYSIS FRAMEWORK:
1. Fact: What actually happened? (primary source)
2. Economic Variable: Which indicator changes?
3. Transmission Channel → Asset Impact (per instrument)
4. Counter-arguments: Why thesis could be wrong
5. Invalidation Conditions: What breaks the thesis
6. Uncertainty: proven/likely/speculative for each claim

INSTRUMENTS: XAUUSD (gold), EURUSD, GBPUSD, WTI (oil)
Analyze each instrument SEPARATELY - do not apply same direction to all.

NEVER predict future price as fact. Express uncertainty explicitly."""

    def build(
        self,
        snapshot: FundamentalSnapshot,
        include_technical: bool = True,
    ) -> tuple[str, str]:
        """
        Build system prompt and user prompt for AI analysis.
        
        Returns:
            (system_prompt, user_prompt)
        """
        user_prompt = self._build_user_prompt(snapshot, include_technical)
        return self.SYSTEM_PROMPT, user_prompt

    def _build_user_prompt(
        self,
        snapshot: FundamentalSnapshot,
        include_technical: bool,
    ) -> str:
        """Build user prompt with snapshot data."""
        lines = []

        lines.append(f"SYMBOL: {snapshot.symbol.value}")
        lines.append(f"TIMESTAMP: {snapshot.timestamp.isoformat()}")
        lines.append(f"DATA QUALITY: {snapshot.quality.value}")
        lines.append("")

        # Tick info
        tick = snapshot.tick_ref
        lines.append("=== CURRENT MARKET STATE ===")
        lines.append(f"Symbol: {tick.canonical_symbol.value}")
        lines.append(f"Bid: {tick.bid} | Ask: {tick.ask} | Mid: {tick.mid}")
        lines.append(f"Spread: {tick.spread} ({tick.spread/tick.mid:.6%} of mid)")
        lines.append(f"Market State: {tick.market_state}")
        lines.append(f"Stale: {tick.stale} | Quote Age: {tick.quote_age_seconds}s")
        lines.append(f"Provider: {tick.provider} | Source: {tick.source}")
        lines.append("")

        # News
        lines.append("=== NEWS ITEMS ===")
        if snapshot.news_refs:
            for i, news in enumerate(snapshot.news_refs[:10], 1):
                lines.append(f"{i}. [{news.published_at.isoformat()}] {news.title}")
                lines.append(f"   Publisher: {news.publisher} | URL: {news.url}")
                lines.append(f"   Instruments: {[t.value for t in news.instrument_tags]}")
                if news.description:
                    lines.append(f"   Summary: {news.description[:200]}...")
                lines.append("")
        else:
            lines.append("No recent news items.")
            lines.append("")

        # Calendar
        lines.append("=== ECONOMIC CALENDAR ===")
        if snapshot.calendar_refs:
            for i, cal in enumerate(snapshot.calendar_refs[:10], 1):
                actual_str = f"Actual: {cal.actual}" if cal.actual else "Actual: pending"
                forecast_str = f"Forecast: {cal.forecast}" if cal.forecast else "Forecast: n/a"
                previous_str = f"Previous: {cal.previous}" if cal.previous else "Previous: n/a"
                lines.append(f"{i}. [{cal.time.isoformat()}] {cal.name} ({cal.currency})")
                lines.append(f"   Impact: {cal.impact.value} | {actual_str} | {forecast_str} | {previous_str}")
                lines.append(f"   Source: {cal.source_url}")
                lines.append("")
        else:
            lines.append("No upcoming calendar events.")
            lines.append("")

        # Technical (optional)
        if include_technical and snapshot.technical_ref:
            tech = snapshot.technical_ref
            lines.append("=== TECHNICAL ANALYSIS ===")
            lines.append(f"H1 Trend: {tech.h1_structure.trend.value}")
            lines.append(f"M15 RSI(14): {tech.m15_rsi}")
            lines.append(f"M15 ATR(14): {tech.m15_atr}")
            lines.append(f"Spread: {tech.spread}")
            if tech.pattern:
                lines.append(f"Pattern: {tech.pattern.pattern_type.value} (bullish={tech.pattern.bullish})")
            if tech.entry_trigger:
                lines.append(f"Entry Trigger: {tech.entry_trigger}")
            if tech.sl:
                lines.append(f"Stop Loss: {tech.sl}")
            if tech.tp1:
                lines.append(f"TP1: {tech.tp1}")
            if tech.tp2:
                lines.append(f"TP2: {tech.tp2}")
            if tech.rr:
                lines.append(f"Risk/Reward: {tech.rr}")
            lines.append("")

        # Analysis instructions
        lines.append("=== ANALYSIS REQUIRED ===")
        lines.append("Analyze the above news and calendar events for fundamental impact.")
        lines.append("For each instrument (XAUUSD, EURUSD, GBPUSD, WTI), determine:")
        lines.append("1. What economic variable changes?")
        lines.append("2. What is the transmission channel to the asset?")
        lines.append("3. What is the likely direction and magnitude?")
        lines.append("4. What are counter-arguments?")
        lines.append("5. What would invalidate this thesis?")
        lines.append("6. What is the uncertainty level?")
        lines.append("")
        lines.append("Return valid JSON only. No markdown, no explanations outside JSON.")

        return "\n".join(lines)


class FundamentalResponseParser:
    """
    Parses AI response into FundamentalThesis.
    Validates structure and handles errors gracefully.
    """

    def parse(
        self,
        ai_response: str,
        model_id: str,
        model_provider: str,
        prompt_version: str = "1.0.0",
    ) -> "FundamentalThesis":
        """
        Parse AI JSON response into FundamentalThesis.
        
        Raises ValueError if response cannot be parsed.
        """
        from ..canonical import (
            FundamentalThesis,
            CausalLink,
            InstrumentImpact,
            Instrument,
        )

        try:
            # Try to extract JSON from response
            json_str = self._extract_json(ai_response)
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            logger.debug(f"Raw response: {ai_response[:500]}")
            # Return a fallback thesis
            return self._fallback_thesis(model_id, model_provider, prompt_version, str(e))

        # Parse thesis
        thesis_text = data.get("thesis", "No thesis provided")
        sources = data.get("sources", [])

        # Parse causal chain
        causal_chain = []
        for link in data.get("causal_chain", []):
            causal_chain.append(CausalLink(
                step=link.get("step", 0),
                description=link.get("description", ""),
                evidence=link.get("evidence", ""),
                confidence=link.get("confidence", "speculative"),
            ))

        # Parse per-instrument impact
        per_instrument = []
        for impact in data.get("per_instrument_impact", []):
            try:
                symbol = Instrument(impact.get("symbol", "XAUUSD"))
            except ValueError:
                symbol = Instrument.XAUUSD

            per_instrument.append(InstrumentImpact(
                symbol=symbol,
                direction=impact.get("direction", "neutral"),
                magnitude=impact.get("magnitude", "weak"),
                reasoning=impact.get("reasoning", ""),
                key_levels=[Decimal(str(l)) for l in impact.get("key_levels", [])],
            ))

        # Parse counter-arguments
        counter_arguments = data.get("counter_arguments", [])

        # Parse invalidation conditions
        invalidation = data.get("invalidation_conditions", [])

        # Parse uncertainty
        uncertainty = data.get("uncertainty_assessment", "Moderate")

        return FundamentalThesis(
            thesis=thesis_text,
            sources=sources,
            causal_chain=causal_chain,
            per_instrument_impact=per_instrument,
            counter_arguments=counter_arguments,
            invalidation_conditions=invalidation,
            uncertainty_assessment=uncertainty,
            model_id=model_id,
            model_provider=model_provider,
            prompt_version=prompt_version,
            generated_at=datetime.now(timezone.utc),
        )

    def _extract_json(self, text: str) -> str:
        """Extract JSON object from text that may contain markdown or extra content."""
        # Try to find JSON block
        text = text.strip()

        # Remove markdown code blocks
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        if "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        # Try to find { ... } block
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return text[start:end + 1]

        return text

    def _fallback_thesis(
        self,
        model_id: str,
        model_provider: str,
        prompt_version: str,
        error: str,
    ) -> "FundamentalThesis":
        """Create fallback thesis when parsing fails."""
        from ..canonical import FundamentalThesis

        return FundamentalThesis(
            thesis=f"AI response parsing failed: {error}",
            sources=[],
            causal_chain=[],
            per_instrument_impact=[],
            counter_arguments=["AI response could not be parsed"],
            invalidation_conditions=["AI response unavailable"],
            uncertainty_assessment="High - AI response parsing failed",
            model_id=model_id,
            model_provider=model_provider,
            prompt_version=prompt_version,
            generated_at=datetime.now(timezone.utc),
        )


# Import logger at module level
import logging
logger = logging.getLogger(__name__)