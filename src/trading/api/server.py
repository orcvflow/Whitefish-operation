"""
REST API Server

Minimal FastAPI server for trading MVP monitoring.
No authentication - internal tool only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

from ..canonical import Instrument, QualityStatus
from ..persistence.store import SQLiteStore
from ..audit.logger import AuditLogger

logger = logging.getLogger(__name__)

# Prometheus metrics
TICKS_RECEIVED = Counter("ticks_received_total", "Total ticks received", ["symbol", "provider"])
TICKS_PERSISTED = Counter("ticks_persisted_total", "Total ticks persisted", ["symbol"])
GAPS_DETECTED = Counter("gaps_detected_total", "Total data gaps detected", ["symbol"])
DECISIONS_TOTAL = Counter("decisions_total", "Total decisions", ["symbol", "status"])
DECISION_LATENCY = Gauge("decision_latency_seconds", "Decision latency by stage", ["stage"])
AI_CALLS = Counter("ai_calls_total", "Total AI calls", ["model", "status"])
AXLE_VERIFICATIONS = Counter("axle_verifications_total", "Total AXLE verifications", ["status"])


def create_app(
    store: SQLiteStore,
    audit_logger: AuditLogger,
) -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="Trading MVP API",
        description="Quantitative Trading MVP - Analysis & Decision API",
        version="0.1.0",
    )

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        try:
            store_stats = store.get_stats()
            integrity_ok, integrity_errors = store.verify_integrity()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return {
            "status": "healthy" if integrity_ok else "degraded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {
                "database": "healthy" if integrity_ok else "unhealthy",
                "store": store_stats,
            },
            "integrity_errors": integrity_errors,
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics():
        """Prometheus metrics endpoint."""
        return generate_latest()

    @app.get("/market/{symbol}")
    async def get_market(symbol: str):
        """Get latest market data for a symbol."""
        try:
            inst = Instrument(symbol)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid symbol: {symbol}")

        tick = store.get_latest_tick(inst)
        if not tick:
            raise HTTPException(status_code=404, detail=f"No data for {symbol}")

        return {
            "symbol": tick.canonical_symbol.value,
            "bid": str(tick.bid),
            "ask": str(tick.ask),
            "mid": str(tick.mid),
            "spread": str(tick.spread),
            "spread_pct": str(tick.spread / tick.mid if tick.mid > 0 else Decimal("0")),
            "high": str(tick.high),
            "low": str(tick.low),
            "market_state": tick.market_state,
            "stale": tick.stale,
            "quote_age_seconds": tick.quote_age_seconds,
            "provider": tick.provider,
            "source": tick.source,
            "provider_ts": tick.provider_ts.isoformat(),
            "received_ts": tick.received_ts.isoformat(),
            "persisted_ts": tick.persisted_ts.isoformat(),
            "quality": tick.quality.value,
        }

    @app.get("/candles/{symbol}")
    async def get_candles(
        symbol: str,
        timeframe: str = Query("1h", description="Timeframe: 1m, 5m, 15m, 30m, 1h, 4h, 1d"),
        limit: int = Query(100, description="Number of bars", le=1000),
        include_open: bool = Query(True, description="Include open bar"),
    ):
        """Get OHLC candles for a symbol."""
        try:
            inst = Instrument(symbol)
            tf = __import__("src.trading.canonical.enums", fromlist=["Timeframe"]).Timeframe(timeframe)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        bars = store.get_ohlc_bars(inst, tf, limit=limit, include_open=include_open)

        return {
            "symbol": inst.value,
            "timeframe": timeframe,
            "count": len(bars),
            "bars": [
                {
                    "open_time": bar.open_time.isoformat(),
                    "close_time": bar.close_time.isoformat(),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": bar.volume,
                    "tick_volume": bar.tick_volume,
                    "is_open": bar.is_open,
                    "quality": bar.quality.value,
                }
                for bar in bars
            ],
        }

    @app.get("/news")
    async def get_news(
        hours: int = Query(24, description="Hours of history", le=720),
        limit: int = Query(50, description="Max items", le=200),
    ):
        """Get recent news items."""
        news = store.get_recent_news(hours=hours, limit=limit)

        return {
            "count": len(news),
            "hours": hours,
            "news": [
                {
                    "id": str(n.id),
                    "provider": n.provider,
                    "title": n.title,
                    "url": n.url,
                    "publisher": n.publisher,
                    "published_at": n.published_at.isoformat(),
                    "instrument_tags": [t.value for t in n.instrument_tags],
                    "quality": n.quality.value,
                }
                for n in news
            ],
        }

    @app.get("/calendar")
    async def get_calendar(
        hours: int = Query(72, description="Hours ahead", le=720),
        currencies: Optional[str] = Query(None, description="Comma-separated currency codes"),
        min_impact: Optional[str] = Query("high", description="Minimum impact level"),
    ):
        """Get upcoming economic calendar events."""
        from datetime import timedelta

        from_ts = datetime.now(timezone.utc)
        to_ts = from_ts + timedelta(hours=hours)

        curr_list = currencies.split(",") if currencies else None

        events = store.get_upcoming_calendar(
            from_ts=from_ts,
            to_ts=to_ts,
            currencies=curr_list,
            min_impact=min_impact,
        )

        return {
            "count": len(events),
            "hours": hours,
            "events": [
                {
                    "id": str(e.id),
                    "event_id": e.event_id,
                    "time": e.time.isoformat(),
                    "currency": e.currency,
                    "name": e.name,
                    "impact": e.impact.value,
                    "actual": str(e.actual) if e.actual else None,
                    "forecast": str(e.forecast) if e.forecast else None,
                    "previous": str(e.previous) if e.previous else None,
                    "source_url": e.source_url,
                    "quality": e.quality.value,
                }
                for e in events
            ],
        }

    @app.get("/analysis/{symbol}")
    async def get_analysis(symbol: str):
        """Get latest analysis for a symbol."""
        try:
            inst = Instrument(symbol)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid symbol: {symbol}")

        tick = store.get_latest_tick(inst)
        if not tick:
            raise HTTPException(status_code=404, detail=f"No data for {symbol}")

        # Get recent OHLC
        h1_bars = store.get_ohlc_bars(inst, __import__("src.trading.canonical.enums", fromlist=["Timeframe"]).Timeframe.H1, limit=100)
        m15_bars = store.get_ohlc_bars(inst, __import__("src.trading.canonical.enums", fromlist=["Timeframe"]).Timeframe.M15, limit=200)

        return {
            "symbol": inst.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tick": {
                "bid": str(tick.bid),
                "ask": str(tick.ask),
                "mid": str(tick.mid),
                "spread": str(tick.spread),
                "market_state": tick.market_state,
                "quality": tick.quality.value,
            },
            "h1_bars": len(h1_bars),
            "m15_bars": len(m15_bars),
            "message": "Full analysis available via decision engine",
        }

    @app.get("/decisions")
    async def get_decisions(
        symbol: Optional[str] = Query(None, description="Filter by symbol"),
        status: Optional[str] = Query(None, description="Filter by status"),
        limit: int = Query(50, description="Max items", le=200),
    ):
        """Get recent trading decisions."""
        decisions = audit_logger.get_decisions(
            symbol=symbol,
            status=status,
            limit=limit,
        )

        return {
            "count": len(decisions),
            "decisions": decisions,
        }

    @app.get("/decisions/{decision_id}")
    async def get_decision(decision_id: str):
        """Get a specific decision with full audit trail."""
        decision = audit_logger.get_decision(decision_id)
        if not decision:
            raise HTTPException(status_code=404, detail=f"Decision not found: {decision_id}")

        return decision

    return app