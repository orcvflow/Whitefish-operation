"""
SQLite WAL Storage for Canonical Data

Crash-safe SQLite storage with WAL mode for canonical market data, news,
calendar events, analysis snapshots, and trading decisions.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Union

from ..canonical import (
    CanonicalTick,
    OHLCBar,
    NewsItem,
    CalendarEvent,
    Instrument,
    Timeframe,
    QualityStatus,
    Decision,
    DecisionStatus,
    VerificationStatus,
    QualityStatus as QS,
    make_duplicate_key,
)


# =============================================================================
# SQLite Connection Wrapper
# =============================================================================

class SQLiteStore:
    """
    SQLite WAL storage for canonical trading data.
    
    Features:
    - WAL mode for concurrent reads/writes
    - Foreign keys enabled
    - Auto-migration via versioned schema
    - Backup and restore
    - Thread-safe via connection pool
    """
    
    SCHEMA_VERSION = 1
    
    def __init__(self, db_path: str, wal_mode: bool = True, sync_on_write: bool = True):
        self.db_path = Path(db_path)
        self.wal_mode = wal_mode
        self.sync_on_write = sync_on_write
        self._lock = threading.RLock()
        
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection with WAL settings."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        
        if self.wal_mode:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL" if not self.sync_on_write else "PRAGMA synchronous=FULL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        
        return conn
    
    def _init_db(self) -> None:
        """Initialize database with schema and migrations."""
        with self._get_connection() as conn:
            self._create_tables(conn)
            self._run_migrations(conn)
    
    def _create_tables(self, conn: sqlite3.Connection) -> None:
        """Create all tables if not exist."""
        
        # Schema version tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        
        # Raw ticks (canonical)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS canonical_ticks (
                event_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                canonical_symbol TEXT NOT NULL,
                provider_ts TEXT NOT NULL,
                received_ts TEXT NOT NULL,
                persisted_ts TEXT NOT NULL,
                bid TEXT NOT NULL,
                ask TEXT NOT NULL,
                mid TEXT NOT NULL,
                spread TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                volume INTEGER NOT NULL,
                direction TEXT NOT NULL,
                day_diff_percent TEXT NOT NULL,
                market_state TEXT NOT NULL,
                stale INTEGER NOT NULL,
                quote_age_seconds INTEGER NOT NULL,
                source TEXT NOT NULL,
                sequence INTEGER,
                raw_payload_hash TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                quality TEXT NOT NULL DEFAULT 'clean',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts 
            ON canonical_ticks(canonical_symbol, provider_ts)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ticks_quality 
            ON canonical_ticks(quality)
        """)
        
        # OHLC bars
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlc_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume INTEGER NOT NULL,
                tick_volume INTEGER NOT NULL,
                is_open INTEGER NOT NULL,
                source TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                quality TEXT NOT NULL DEFAULT 'clean',
                provider_bar_id TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(symbol, timeframe, open_time)
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ohlc_symbol_tf_ts 
            ON ohlc_bars(symbol, timeframe, open_time)
        """)
        
        # News items
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_items (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                publisher TEXT NOT NULL,
                published_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                description TEXT NOT NULL,
                image_url TEXT,
                category TEXT,
                language TEXT NOT NULL,
                country TEXT NOT NULL,
                instrument_tags TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                quality TEXT NOT NULL DEFAULT 'clean',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_published 
            ON news_items(published_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_content_hash 
            ON news_items(content_hash)
        """)
        
        # Calendar events
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                time TEXT NOT NULL,
                period TEXT NOT NULL,
                country_code TEXT NOT NULL,
                currency TEXT NOT NULL,
                name TEXT NOT NULL,
                impact TEXT NOT NULL,
                event_type TEXT NOT NULL,
                sector TEXT NOT NULL,
                unit TEXT NOT NULL,
                multiplier TEXT NOT NULL,
                digits INTEGER NOT NULL,
                actual TEXT,
                forecast TEXT,
                previous TEXT,
                revised_previous TEXT,
                revision INTEGER NOT NULL DEFAULT 1,
                time_mode TEXT NOT NULL,
                source_url TEXT NOT NULL,
                quality TEXT NOT NULL DEFAULT 'clean',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(provider, event_id)
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_calendar_ts 
            ON calendar_events(ts)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_calendar_currency_impact 
            ON calendar_events(currency, impact)
        """)
        
        # Analysis snapshots
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quality TEXT NOT NULL DEFAULT 'clean',
                tick_event_id TEXT,
                technical_analysis_id TEXT,
                fundamental_thesis_id TEXT,
                snapshot_data TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshot_symbol_ts 
            ON analysis_snapshots(symbol, timestamp)
        """)
        
        # Fundamental theses
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamental_theses (
                thesis_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                thesis TEXT NOT NULL,
                sources TEXT,
                causal_chain TEXT,
                per_instrument_impact TEXT,
                counter_arguments TEXT,
                invalidation_conditions TEXT,
                uncertainty_assessment TEXT,
                model_id TEXT,
                model_provider TEXT,
                prompt_version TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                FOREIGN KEY (snapshot_id) REFERENCES analysis_snapshots(snapshot_id)
            )
        """)
        
        # Trading decisions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                entry TEXT,
                sl TEXT,
                tp1 TEXT,
                tp2 TEXT,
                risk_pct TEXT,
                rr TEXT,
                failed_gates TEXT,
                strategy_version TEXT NOT NULL,
                data_quality TEXT NOT NULL,
                axle_verification TEXT,
                fundamental_thesis_id TEXT,
                technical_analysis_id TEXT,
                position_size TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                FOREIGN KEY (snapshot_id) REFERENCES analysis_snapshots(snapshot_id)
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_decisions_symbol_ts 
            ON decisions(symbol, timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_decisions_status 
            ON decisions(status)
        """)
        
        # News IDs per decision (many-to-many)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_news (
                decision_id TEXT NOT NULL,
                news_id TEXT NOT NULL,
                PRIMARY KEY (decision_id, news_id),
                FOREIGN KEY (decision_id) REFERENCES decisions(decision_id),
                FOREIGN KEY (news_id) REFERENCES news_items(id)
            )
        """)
        
        # Calendar IDs per decision (many-to-many)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_calendar (
                decision_id TEXT NOT NULL,
                calendar_id TEXT NOT NULL,
                PRIMARY KEY (decision_id, calendar_id),
                FOREIGN KEY (decision_id) REFERENCES decisions(decision_id),
                FOREIGN KEY (calendar_id) REFERENCES calendar_events(id)
            )
        """)
        
        # AXLE verification results
        conn.execute("""
            CREATE TABLE IF NOT EXISTS axle_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL,
                check_name TEXT NOT NULL,
                status TEXT NOT NULL,
                lean_code TEXT,
                lean_output TEXT,
                error TEXT,
                duration_ms INTEGER,
                verified_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
            )
        """)
        
        conn.commit()
    
    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Run pending schema migrations."""
        # Get current version
        row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        current_version = row[0] if row and row[0] else 0
        
        if current_version >= self.SCHEMA_VERSION:
            return
        
        # Migration 1: Initial schema already created
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (self.SCHEMA_VERSION, datetime.utcnow().isoformat() + "Z"),
        )
        conn.commit()
    
    # =============================================================================
    # Tick Operations
    # =============================================================================
    
    def upsert_tick(self, tick: CanonicalTick) -> bool:
        """
        Insert or update a canonical tick.
        Returns True if inserted, False if duplicate (ignored).
        """
        with self._lock:
            try:
                with self._get_connection() as conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO canonical_ticks (
                            event_id, provider, provider_symbol, canonical_symbol,
                            provider_ts, received_ts, persisted_ts,
                            bid, ask, mid, spread, high, low,
                            volume, direction, day_diff_percent,
                            market_state, stale, quote_age_seconds,
                            source, sequence, raw_payload_hash,
                            schema_version, quality
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(tick.event_id), tick.provider, tick.provider_symbol,
                        tick.canonical_symbol.value,
                        tick.provider_ts.isoformat(), tick.received_ts.isoformat(),
                        tick.persisted_ts.isoformat(),
                        str(tick.bid), str(tick.ask), str(tick.mid), str(tick.spread),
                        str(tick.high), str(tick.low),
                        tick.volume, tick.direction, str(tick.day_diff_percent),
                        tick.market_state, 1 if tick.stale else 0,
                        tick.quote_age_seconds, tick.source,
                        tick.sequence, tick.raw_payload_hash,
                        tick.schema_version, tick.quality.value,
                    ))
                    conn.commit()
                    return conn.total_changes > 0
            except sqlite3.IntegrityError:
                return False
    
    def get_ticks(
        self,
        symbol: Instrument,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[CanonicalTick]:
        """Get canonical ticks for a symbol within time range."""
        with self._lock:
            with self._get_connection() as conn:
                query = """
                    SELECT * FROM canonical_ticks 
                    WHERE canonical_symbol = ?
                """
                params: list[Any] = [symbol.value]
                
                if from_ts:
                    query += " AND provider_ts >= ?"
                    params.append(from_ts.isoformat())
                if to_ts:
                    query += " AND provider_ts <= ?"
                    params.append(to_ts.isoformat())
                
                query += " ORDER BY provider_ts DESC LIMIT ?"
                params.append(limit)
                
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_tick(row) for row in rows]
    
    def get_latest_tick(self, symbol: Instrument) -> Optional[CanonicalTick]:
        """Get the most recent canonical tick for a symbol."""
        ticks = self.get_ticks(symbol, limit=1)
        return ticks[0] if ticks else None
    
    def _row_to_tick(self, row: sqlite3.Row) -> CanonicalTick:
        """Convert database row to CanonicalTick."""
        return CanonicalTick(
            event_id=__import__("uuid").UUID(row["event_id"]),
            provider=row["provider"],
            provider_symbol=row["provider_symbol"],
            canonical_symbol=Instrument(row["canonical_symbol"]),
            provider_ts=datetime.fromisoformat(row["provider_ts"].replace("Z", "+00:00")),
            received_ts=datetime.fromisoformat(row["received_ts"].replace("Z", "+00:00")),
            persisted_ts=datetime.fromisoformat(row["persisted_ts"].replace("Z", "+00:00")),
            bid=Decimal(row["bid"]),
            ask=Decimal(row["ask"]),
            mid=Decimal(row["mid"]),
            spread=Decimal(row["spread"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            volume=row["volume"],
            direction=row["direction"],
            day_diff_percent=Decimal(row["day_diff_percent"]),
            market_state=row["market_state"],
            stale=bool(row["stale"]),
            quote_age_seconds=row["quote_age_seconds"],
            source=row["source"],
            sequence=row["sequence"],
            raw_payload_hash=row["raw_payload_hash"],
            schema_version=row["schema_version"],
            quality=QualityStatus(row["quality"]),
        )
    
    # =============================================================================
    # OHLC Bar Operations
    # =============================================================================
    
    def upsert_ohlc(self, bar: OHLCBar) -> bool:
        """Insert or update an OHLC bar."""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    conn.execute("""
                        INSERT INTO ohlc_bars (
                            symbol, timeframe, open_time, close_time,
                            open, high, low, close,
                            volume, tick_volume, is_open,
                            source, revision, quality, provider_bar_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(symbol, timeframe, open_time) DO UPDATE SET
                            close = excluded.close,
                            high = excluded.high,
                            low = excluded.low,
                            volume = excluded.volume,
                            tick_volume = excluded.tick_volume,
                            is_open = excluded.is_open,
                            revision = excluded.revision,
                            quality = excluded.quality,
                            provider_bar_id = excluded.provider_bar_id
                    """, (
                        bar.symbol.value, bar.timeframe.value,
                        bar.open_time.isoformat(), bar.close_time.isoformat(),
                        str(bar.open), str(bar.high), str(bar.low), str(bar.close),
                        bar.volume, bar.tick_volume, 1 if bar.is_open else 0,
                        bar.source, bar.revision, bar.quality.value,
                        bar.provider_bar_id,
                    ))
                    conn.commit()
                    return True
            except sqlite3.Error:
                return False
    
    def get_ohlc_bars(
        self,
        symbol: Instrument,
        timeframe: Timeframe,
        limit: int = 500,
        include_open: bool = True,
    ) -> list[OHLCBar]:
        """Get OHLC bars for symbol/timeframe, most recent first."""
        with self._lock:
            with self._get_connection() as conn:
                query = """
                    SELECT * FROM ohlc_bars 
                    WHERE symbol = ? AND timeframe = ?
                """
                params: list[Any] = [symbol.value, timeframe.value]
                
                if not include_open:
                    query += " AND is_open = 0"
                
                query += " ORDER BY open_time DESC LIMIT ?"
                params.append(limit)
                
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_ohlc(row) for row in rows]
    
    def get_closed_ohlc_bars(
        self,
        symbol: Instrument,
        timeframe: Timeframe,
        limit: int = 500,
    ) -> list[OHLCBar]:
        """Get only closed (completed) OHLC bars."""
        return self.get_ohlc_bars(symbol, timeframe, limit, include_open=False)
    
    def _row_to_ohlc(self, row: sqlite3.Row) -> OHLCBar:
        """Convert database row to OHLCBar."""
        return OHLCBar(
            symbol=Instrument(row["symbol"]),
            timeframe=Timeframe(row["timeframe"]),
            open_time=datetime.fromisoformat(row["open_time"].replace("Z", "+00:00")),
            close_time=datetime.fromisoformat(row["close_time"].replace("Z", "+00:00")),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=row["volume"],
            tick_volume=row["tick_volume"],
            is_open=bool(row["is_open"]),
            source=row["source"],
            revision=row["revision"],
            quality=QualityStatus(row["quality"]),
            provider_bar_id=row["provider_bar_id"],
        )
    
    # =============================================================================
    # News Operations
    # =============================================================================
    
    def upsert_news(self, news: NewsItem) -> bool:
        """Insert or update a news item."""
        from ..canonical import uuid
        
        with self._lock:
            try:
                with self._get_connection() as conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO news_items (
                            id, provider, title, url, publisher,
                            published_at, received_at, description,
                            image_url, category, language, country,
                            instrument_tags, content_hash, quality
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(news.id), news.provider, news.title, news.url,
                        news.publisher, news.published_at.isoformat(),
                        news.received_at.isoformat(), news.description,
                        news.image_url, news.category, news.language,
                        news.country,
                        json.dumps([s.value for s in news.instrument_tags]),
                        news.content_hash, news.quality.value,
                    ))
                    conn.commit()
                    return conn.total_changes > 0
            except sqlite3.IntegrityError:
                return False
    
    def get_recent_news(
        self,
        hours: int = 24,
        limit: int = 100,
    ) -> list[NewsItem]:
        """Get recent news items within hours."""
        from datetime import timedelta
        from ..canonical import uuid
        
        from_ts = datetime.utcnow() - timedelta(hours=hours)
        
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT * FROM news_items 
                    WHERE published_at >= ?
                    ORDER BY published_at DESC LIMIT ?
                """, (from_ts.isoformat(), limit)).fetchall()
                return [self._row_to_news(row) for row in rows]
    
    def _row_to_news(self, row: sqlite3.Row) -> NewsItem:
        """Convert database row to NewsItem."""
        return NewsItem(
            id=__import__("uuid").UUID(row["id"]),
            provider=row["provider"],
            title=row["title"],
            url=row["url"],
            publisher=row["publisher"],
            published_at=datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")),
            received_at=datetime.fromisoformat(row["received_at"].replace("Z", "+00:00")),
            description=row["description"],
            image_url=row["image_url"],
            category=row["category"],
            language=row["language"],
            country=row["country"],
            instrument_tags=[Instrument(s) for s in json.loads(row["instrument_tags"])],
            content_hash=row["content_hash"],
            quality=QualityStatus(row["quality"]),
        )
    
    # =============================================================================
    # Calendar Operations
    # =============================================================================
    
    def upsert_calendar(self, event: CalendarEvent) -> bool:
        """Insert or update a calendar event."""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO calendar_events (
                            id, provider, event_id, ts, time, period,
                            country_code, currency, name, impact,
                            event_type, sector, unit, multiplier,
                            digits, actual, forecast, previous,
                            revised_previous, revision, time_mode, source_url, quality
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(event.id), event.provider, event.event_id, event.ts,
                        event.time.isoformat(), event.period.isoformat(),
                        event.country_code, event.currency, event.name,
                        event.impact.value, event.event_type, event.sector,
                        event.unit, event.multiplier, event.digits,
                        str(event.actual) if event.actual else None,
                        str(event.forecast) if event.forecast else None,
                        str(event.previous) if event.previous else None,
                        str(event.revised_previous) if event.revised_previous else None,
                        event.revision, event.time_mode, event.source_url,
                        event.quality.value,
                    ))
                    conn.commit()
                    return conn.total_changes > 0
            except sqlite3.IntegrityError:
                return False
    
    def get_upcoming_calendar(
        self,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
        currencies: Optional[list[str]] = None,
        min_impact: Optional[str] = None,
        limit: int = 100,
    ) -> list[CalendarEvent]:
        """Get calendar events within time range."""
        from ..canonical import uuid
        
        with self._lock:
            with self._get_connection() as conn:
                query = "SELECT * FROM calendar_events WHERE 1=1"
                params: list[Any] = []
                
                if from_ts:
                    query += " AND ts >= ?"
                    params.append(int(from_ts.timestamp()))
                if to_ts:
                    query += " AND ts <= ?"
                    params.append(int(to_ts.timestamp()))
                if currencies:
                    placeholders = ",".join("?" * len(currencies))
                    query += f" AND currency IN ({placeholders})"
                    params.extend(currencies)
                if min_impact:
                    impact_order = {"low": 1, "medium": 2, "high": 3, "holiday": 4}
                    min_level = impact_order.get(min_impact, 0)
                    query += " AND impact IN ("
                    query += ",".join("?" * len([k for k, v in impact_order.items() if v >= min_level]))
                    query += ")"
                    params.extend([k for k, v in impact_order.items() if v >= min_level])
                
                query += " ORDER BY ts ASC LIMIT ?"
                params.append(limit)
                
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_calendar(row) for row in rows]
    
    def _row_to_calendar(self, row: sqlite3.Row) -> CalendarEvent:
        """Convert database row to CalendarEvent."""
        return CalendarEvent(
            id=__import__("uuid").UUID(row["id"]),
            provider=row["provider"],
            event_id=row["event_id"],
            ts=row["ts"],
            time=datetime.fromisoformat(row["time"].replace("Z", "+00:00")),
            period=datetime.fromisoformat(row["period"].replace("Z", "+00:00")),
            country_code=row["country_code"],
            currency=row["currency"],
            name=row["name"],
            impact=__import__("src.trading.canonical.enums", fromlist=["NewsImpact"]).NewsImpact(row["impact"]),
            event_type=row["event_type"],
            sector=row["sector"],
            unit=row["unit"],
            multiplier=row["multiplier"],
            digits=row["digits"],
            actual=Decimal(row["actual"]) if row["actual"] else None,
            forecast=Decimal(row["forecast"]) if row["forecast"] else None,
            previous=Decimal(row["previous"]) if row["previous"] else None,
            revised_previous=Decimal(row["revised_previous"]) if row["revised_previous"] else None,
            revision=row["revision"],
            time_mode=row["time_mode"],
            source_url=row["source_url"],
            quality=QualityStatus(row["quality"]),
        )
    
    # =============================================================================
    # Decision Operations
    # =============================================================================
    
    def save_decision(self, decision: Decision) -> bool:
        """Save a trading decision with full audit trail."""
        from ..canonical import uuid
        
        with self._lock:
            try:
                with self._get_connection() as conn:
                    # Save decision
                    conn.execute("""
                        INSERT INTO decisions (
                            decision_id, snapshot_id, symbol, status, timestamp,
                            entry, sl, tp1, tp2, risk_pct, rr,
                            failed_gates, strategy_version, data_quality,
                            axle_verification, fundamental_thesis_id,
                            technical_analysis_id, position_size, notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(decision.decision_id), str(decision.snapshot_id),
                        decision.symbol.value, decision.status.value,
                        decision.timestamp.isoformat(),
                        str(decision.entry) if decision.entry else None,
                        str(decision.sl) if decision.sl else None,
                        str(decision.tp1) if decision.tp1 else None,
                        str(decision.tp2) if decision.tp2 else None,
                        str(decision.risk_pct) if decision.risk_pct else None,
                        str(decision.rr) if decision.rr else None,
                        json.dumps([g.value for g in decision.failed_gates]),
                        decision.strategy_version, decision.data_quality.value,
                        json.dumps({k: v.value for k, v in decision.axle_verification.items()}),
                        str(decision.fundamental_thesis_id) if decision.fundamental_thesis_id else None,
                        str(decision.technical_analysis_id) if decision.technical_analysis_id else None,
                        str(decision.position_size) if decision.position_size else None,
                        decision.notes,
                    ))
                    
                    # Save news links
                    for news_id in decision.news_ids:
                        conn.execute("""
                            INSERT OR IGNORE INTO decision_news (decision_id, news_id)
                            VALUES (?, ?)
                        """, (str(decision.decision_id), str(news_id)))
                    
                    # Save calendar links
                    for cal_id in decision.calendar_ids:
                        conn.execute("""
                            INSERT OR IGNORE INTO decision_calendar (decision_id, calendar_id)
                            VALUES (?, ?)
                        """, (str(decision.decision_id), str(cal_id)))
                    
                    conn.commit()
                    return True
            except sqlite3.Error:
                return False
    
    def get_decision(self, decision_id: uuid.UUID) -> Optional[Decision]:
        """Get a decision by ID."""
        import uuid as uuid_mod
        
        if isinstance(decision_id, str):
            decision_id = uuid_mod.UUID(decision_id)
        
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM decisions WHERE decision_id = ?",
                    (str(decision_id),)
                ).fetchone()
                
                if not row:
                    return None
                
                return self._row_to_decision(row, conn)
    
    def get_decisions(
        self,
        symbol: Optional[Instrument] = None,
        status: Optional[DecisionStatus] = None,
        limit: int = 100,
    ) -> list[Decision]:
        """Get decisions with optional filters."""
        import uuid as uuid_mod
        
        with self._lock:
            with self._get_connection() as conn:
                query = "SELECT * FROM decisions WHERE 1=1"
                params: list[Any] = []
                
                if symbol:
                    query += " AND symbol = ?"
                    params.append(symbol.value)
                if status:
                    query += " AND status = ?"
                    params.append(status.value)
                
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_decision(row, conn) for row in rows]
    
    def _row_to_decision(self, row: sqlite3.Row, conn: sqlite3.Connection) -> Decision:
        """Convert database row to Decision."""
        import uuid as uuid_mod
        
        # Get linked news and calendar IDs
        news_rows = conn.execute(
            "SELECT news_id FROM decision_news WHERE decision_id = ?",
            (row["decision_id"],)
        ).fetchall()
        news_ids = [uuid_mod.UUID(r["news_id"]) for r in news_rows]
        
        cal_rows = conn.execute(
            "SELECT calendar_id FROM decision_calendar WHERE decision_id = ?",
            (row["decision_id"],)
        ).fetchall()
        cal_ids = [uuid_mod.UUID(r["calendar_id"]) for r in cal_rows]
        
        axle_verification = {}
        if row["axle_verification"]:
            for k, v in json.loads(row["axle_verification"]).items():
                axle_verification[k] = VerificationStatus(v)
        
        return Decision(
            decision_id=uuid_mod.UUID(row["decision_id"]),
            snapshot_id=uuid_mod.UUID(row["snapshot_id"]),
            symbol=Instrument(row["symbol"]),
            status=DecisionStatus(row["status"]),
            timestamp=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
            news_ids=news_ids,
            calendar_ids=cal_ids,
            entry=Decimal(row["entry"]) if row["entry"] else None,
            sl=Decimal(row["sl"]) if row["sl"] else None,
            tp1=Decimal(row["tp1"]) if row["tp1"] else None,
            tp2=Decimal(row["tp2"]) if row["tp2"] else None,
            risk_pct=Decimal(row["risk_pct"]) if row["risk_pct"] else None,
            rr=Decimal(row["rr"]) if row["rr"] else None,
            failed_gates=[__import__("src.trading.canonical.enums", fromlist=["GateType"]).GateType(g) for g in json.loads(row["failed_gates"])],
            strategy_version=row["strategy_version"],
            data_quality=QualityStatus(row["data_quality"]),
            axle_verification=axle_verification,
            fundamental_thesis_id=uuid_mod.UUID(row["fundamental_thesis_id"]) if row["fundamental_thesis_id"] else None,
            technical_analysis_id=uuid_mod.UUID(row["technical_analysis_id"]) if row["technical_analysis_id"] else None,
            position_size=Decimal(row["position_size"]) if row["position_size"] else None,
            notes=row["notes"] or "",
        )
    
    # =============================================================================
    # AXLE Verification
    # =============================================================================
    
    def save_axle_verification(
        self,
        decision_id: uuid.UUID,
        check_name: str,
        status: VerificationStatus,
        lean_code: Optional[str] = None,
        lean_output: Optional[str] = None,
        error: Optional[str] = None,
        duration_ms: int = 0,
    ) -> None:
        """Save AXLE verification result."""
        import uuid as uuid_mod
        
        if isinstance(decision_id, str):
            decision_id = uuid_mod.UUID(decision_id)
        
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO axle_verifications (
                        decision_id, check_name, status,
                        lean_code, lean_output, error, duration_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(decision_id), check_name, status.value,
                    lean_code, lean_output, error, duration_ms,
                ))
                conn.commit()
    
    # =============================================================================
    # Backup and Recovery
    # =============================================================================
    
    def backup(self, backup_path: str) -> str:
        """Create a backup of the database."""
        backup = Path(backup_path)
        backup.parent.mkdir(parents=True, exist_ok=True)
        
        with self._lock:
            # Use SQLite backup API for consistency
            source = self._get_connection()
            dest = sqlite3.connect(str(backup))
            source.backup(dest)
            dest.close()
            source.close()
        
        return str(backup)
    
    def restore(self, backup_path: str) -> None:
        """Restore database from backup."""
        backup = Path(backup_path)
        if not backup.exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")
        
        with self._lock:
            shutil.copy2(str(backup), str(self.db_path))
    
    def verify_integrity(self) -> tuple[bool, list[str]]:
        """Verify database integrity."""
        errors = []
        
        with self._lock:
            with self._get_connection() as conn:
                # Check integrity
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result[0] != "ok":
                    errors.append(f"Integrity check failed: {result[0]}")
                
                # Check foreign keys
                fk_result = conn.execute("PRAGMA foreign_key_check").fetchall()
                if fk_result:
                    for row in fk_result:
                        errors.append(f"Foreign key violation: {row}")
        
        return len(errors) == 0, errors
    
    def get_stats(self) -> dict[str, Any]:
        """Get database statistics."""
        with self._lock:
            with self._get_connection() as conn:
                stats = {}
                
                for table in ["canonical_ticks", "ohlc_bars", "news_items", 
                              "calendar_events", "analysis_snapshots", "decisions"]:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    stats[table] = count
                
                # Database size
                stats["db_size_bytes"] = self.db_path.stat().st_size if self.db_path.exists() else 0
                stats["schema_version"] = self.SCHEMA_VERSION
                
                return stats
    
    def close(self) -> None:
        """Close all connections (no-op for per-call connections)."""
        pass