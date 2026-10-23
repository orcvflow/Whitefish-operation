"""
Fundamental Analysis Engine

Orchestrates AI-driven fundamental analysis of news and calendar data.
Produces structured FundamentalThesis with causal chain and per-instrument impact.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from ..canonical import (
    Instrument,
    CanonicalTick,
    NewsItem,
    CalendarEvent,
    FundamentalThesis,
    FundamentalSnapshot,
    QualityStatus,
    TechnicalAnalysis,
)
from ..persistence.store import SQLiteStore
from .ai_adapter import AIAdapter, create_ai_adapter, AIConfig
from .snapshot import (
    FundamentalSnapshotBuilder,
    FundamentalPromptBuilder,
    FundamentalResponseParser,
)

logger = logging.getLogger(__name__)


class FundamentalEngine:
    """
    Fundamental analysis engine.
    
    Flow:
    1. Build snapshot from news, calendar, market data
    2. Construct structured prompt
    3. Call AI model (Ling-3.0-flash-Fin via OpenRouter)
    4. Parse and validate response
    5. Store thesis + raw response for audit
    6. Return structured FundamentalThesis
    
    AI never calculates indicators, SL/TP, or position size.
    AI only provides fundamental thesis with uncertainty assessment.
    """

    def __init__(
        self,
        store: SQLiteStore,
        ai_adapter: Optional[AIAdapter] = None,
        config: Optional[AIConfig] = None,
        api_key: Optional[str] = None,
        mock: bool = False,
    ):
        self.store = store
        self.config = config or AIConfig()
        self.mock = mock

        if ai_adapter:
            self.ai_adapter = ai_adapter
        else:
            self.ai_adapter = create_ai_adapter(
                self.config,
                api_key=api_key,
                mock=mock,
            )

        self.snapshot_builder = FundamentalSnapshotBuilder()
        self.prompt_builder = FundamentalPromptBuilder()
        self.response_parser = FundamentalResponseParser()

        self._analysis_count = 0
        self._error_count = 0

    async def analyze(
        self,
        symbol: Instrument,
        tick: CanonicalTick,
        news: list[NewsItem],
        calendar: list[CalendarEvent],
        technical: Optional[TechnicalAnalysis] = None,
    ) -> FundamentalThesis:
        """
        Run fundamental analysis on symbol with given context.
        
        Args:
            symbol: Trading instrument
            tick: Latest market tick
            news: Relevant news items
            calendar: Relevant calendar events
            technical: Optional technical analysis for context
            
        Returns:
            FundamentalThesis with structured analysis
        """
        self._analysis_count += 1

        # Build immutable snapshot
        snapshot = self.snapshot_builder.build(
            symbol=symbol,
            tick=tick,
            news=news,
            calendar=calendar,
            technical=technical,
        )

        # Build prompts
        system_prompt, user_prompt = self.prompt_builder.build(snapshot)

        # Call AI
        try:
            ai_response = await self.ai_adapter.analyze(
                prompt=user_prompt,
                system_prompt=system_prompt,
            )
        except Exception as e:
            logger.error(f"AI analysis failed for {symbol.value}: {e}")
            self._error_count += 1
            # Return fallback thesis
            return self._fallback_thesis(symbol, str(e))

        # Parse response
        try:
            thesis = self.response_parser.parse(
                ai_response.content,
                model_id=ai_response.model,
                model_provider=ai_response.provider,
            )
        except Exception as e:
            logger.error(f"AI response parsing failed for {symbol.value}: {e}")
            self._error_count += 1
            thesis = self.response_parser._fallback_thesis(
                ai_response.model,
                ai_response.provider,
                "1.0.0",
                str(e),
            )

        # Store for audit (snapshot + thesis reference)
        self._store_snapshot(snapshot, thesis)

        logger.info(
            f"Fundamental analysis complete for {symbol.value}: "
            f"thesis={thesis.thesis[:80]}... | "
            f"uncertainty={thesis.uncertainty_assessment} | "
            f"model={thesis.model_id}"
        )

        return thesis

    def _store_snapshot(
        self,
        snapshot: FundamentalSnapshot,
        thesis: FundamentalThesis,
    ) -> None:
        """Store snapshot and thesis for audit trail."""
        try:
            snapshot_data = {
                "snapshot_id": str(snapshot.snapshot_id),
                "timestamp": snapshot.timestamp.isoformat(),
                "symbol": snapshot.symbol.value,
                "quality": snapshot.quality.value,
                "tick": {
                    "symbol": snapshot.tick_ref.canonical_symbol.value,
                    "bid": str(snapshot.tick_ref.bid),
                    "ask": str(snapshot.tick_ref.ask),
                    "mid": str(snapshot.tick_ref.mid),
                    "spread": str(snapshot.tick_ref.spread),
                    "market_state": snapshot.tick_ref.market_state,
                },
                "news_count": len(snapshot.news_refs),
                "calendar_count": len(snapshot.calendar_refs),
                "has_technical": snapshot.technical_ref is not None,
            }

            with self.store._get_connection() as conn:
                conn.execute("""
                    INSERT INTO analysis_snapshots (
                        snapshot_id, timestamp, symbol, quality,
                        snapshot_data
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    str(snapshot.snapshot_id),
                    snapshot.timestamp.isoformat(),
                    snapshot.symbol.value,
                    snapshot.quality.value,
                    json.dumps(snapshot_data),
                ))

                thesis_data = {
                    "thesis": thesis.thesis,
                    "sources": thesis.sources,
                    "causal_chain": [
                        {
                            "step": link.step,
                            "description": link.description,
                            "evidence": link.evidence,
                            "confidence": link.confidence,
                        }
                        for link in thesis.causal_chain
                    ],
                    "per_instrument_impact": [
                        {
                            "symbol": imp.symbol.value,
                            "direction": imp.direction,
                            "magnitude": imp.magnitude,
                            "reasoning": imp.reasoning,
                            "key_levels": [str(l) for l in imp.key_levels],
                        }
                        for imp in thesis.per_instrument_impact
                    ],
                    "counter_arguments": thesis.counter_arguments,
                    "invalidation_conditions": thesis.invalidation_conditions,
                    "uncertainty_assessment": thesis.uncertainty_assessment,
                    "model_id": thesis.model_id,
                    "model_provider": thesis.model_provider,
                }

                conn.execute("""
                    INSERT OR REPLACE INTO fundamental_theses (
                        thesis_id, snapshot_id, thesis_data, created_at
                    ) VALUES (?, ?, ?, ?)
                """, (
                    str(uuid.uuid4()),
                    str(snapshot.snapshot_id),
                    json.dumps(thesis_data),
                    datetime.now(timezone.utc).isoformat(),
                ))

                conn.commit()

        except Exception as e:
            logger.error(f"Failed to store snapshot/thesis: {e}")

    def _fallback_thesis(
        self,
        symbol: Instrument,
        error: str,
    ) -> FundamentalThesis:
        """Create fallback thesis when AI is unavailable."""
        return FundamentalThesis(
            thesis=f"AI analysis unavailable: {error}",
            sources=[],
            causal_chain=[],
            per_instrument_impact=[],
            counter_arguments=["AI analysis failed"],
            invalidation_conditions=["AI analysis unavailable"],
            uncertainty_assessment="High - AI analysis unavailable",
            model_id="unavailable",
            model_provider="unavailable",
            prompt_version="1.0.0",
            generated_at=datetime.now(timezone.utc),
        )

    async def get_impact(
        self,
        thesis: FundamentalThesis,
        symbol: Instrument,
    ) -> Optional[dict]:
        """Get impact assessment for specific instrument from thesis."""
        for impact in thesis.per_instrument_impact:
            if impact.symbol == symbol:
                return {
                    "direction": impact.direction,
                    "magnitude": impact.magnitude,
                    "reasoning": impact.reasoning,
                    "key_levels": [str(l) for l in impact.key_levels],
                }
        return None

    async def close(self) -> None:
        """Cleanup AI adapter resources."""
        await self.ai_adapter.close()

    @property
    def stats(self) -> dict:
        """Get engine statistics."""
        return {
            "analysis_count": self._analysis_count,
            "error_count": self._error_count,
            "model": self.config.model,
            "provider": self.config.provider,
            "mock": self.mock,
        }