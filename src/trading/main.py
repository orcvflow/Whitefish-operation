"""
Trading MVP - Main Entry Point

Modular monolith for fundamental-technical trading analysis.
No live trading - analysis, decision plans, simulation, and audit only.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Optional

import uvicorn

from .config import load_config, get_settings
from .canonical import Instrument
from .persistence import EventJournal, SQLiteStore
from .ingestion import BiquoteWebSocketClient, IngestionPipeline
from .fundamental import FundamentalEngine, create_ai_adapter, AIConfig
from .technical import TechnicalEngine, TechnicalConfig
from .risk import RiskEngine
from .canonical import RiskConfig
from .decision import DecisionEngine
from .axle import AXLEVerifier
from .audit import AuditLogger
from .api import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("trading-mvp")


class TradingMVP:
    """
    Main application class.
    
    Orchestrates all components:
    - Market data ingestion (WebSocket + REST)
    - Persistence (WAL journal + SQLite)
    - Fundamental analysis (AI)
    - Technical analysis (deterministic)
    - Risk engine (hard gates)
    - Decision engine (OPEN/WAIT/SKIP)
    - Audit logging
    - REST API
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.config = None
        self.app_config = None

        # Components
        self.journal: Optional[EventJournal] = None
        self.store: Optional[SQLiteStore] = None
        self.ingestion: Optional[IngestionPipeline] = None
        self.fundamental_engine: Optional[FundamentalEngine] = None
        self.technical_engine: Optional[TechnicalEngine] = None
        self.risk_engine: Optional[RiskEngine] = None
        self.decision_engine: Optional[DecisionEngine] = None
        self.axle_verifier: Optional[AXLEVerifier] = None
        self.audit_logger: Optional[AuditLogger] = None

        # Runtime state
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing Trading MVP...")

        # Load configuration
        self.app_config = load_config(self.config_path)
        self.settings = get_settings()

        logger.info(f"Configuration loaded: {len(self.app_config.instruments)} instruments")

        # Initialize persistence
        self.journal = EventJournal(
            path=self.app_config.persistence.journal_path,
            max_size_mb=self.app_config.persistence.journal_max_size_mb,
            rotation=self.app_config.persistence.journal_rotation,
            sync_on_write=self.app_config.persistence.sync_on_write,
        )

        self.store = SQLiteStore(
            db_path=self.app_config.persistence.db_path,
            wal_mode=self.app_config.persistence.wal_mode,
            sync_on_write=self.app_config.persistence.sync_on_write,
        )

        logger.info("Persistence initialized")

        # Initialize audit logger
        self.audit_logger = AuditLogger(
            store=self.store,
            journal_path=self.app_config.persistence.journal_path,
        )

        logger.info("Audit logger initialized")

        # Initialize technical engine
        self.technical_engine = TechnicalEngine(config=self.app_config.technical)
        logger.info("Technical engine initialized")

        # Initialize risk engine
        self.risk_engine = RiskEngine(config=self.app_config.risk)
        logger.info("Risk engine initialized")

        # Initialize AXLE verifier
        self.axle_verifier = AXLEVerifier(
            api_key=self.settings.axle_api_key,
            timeout_seconds=self.app_config.ai.timeout_seconds,
            enabled=self.app_config.ai.provider == "openrouter",
        )
        logger.info("AXLE verifier initialized")

        # Initialize fundamental engine
        self.fundamental_engine = FundamentalEngine(
            store=self.store,
            config=self.app_config.ai,
            api_key=self.settings.openrouter_api_key,
            mock=True,  # Set to False when API key is configured
        )
        logger.info("Fundamental engine initialized")

        # Initialize decision engine
        self.decision_engine = DecisionEngine(
            risk_config=self.app_config.risk,
            axle_verifier=self.axle_verifier,
            strategy_version="1.0.0",
        )
        logger.info("Decision engine initialized")

        # Initialize ingestion pipeline
        self.ingestion = IngestionPipeline(
            journal=self.journal,
            store=self.store,
            symbols=[i.value for i in self.app_config.instruments],
        )
        logger.info("Ingestion pipeline initialized")

        logger.info("Trading MVP initialized successfully")

    async def start(self) -> None:
        """Start all components."""
        if self._running:
            logger.warning("Already running")
            return

        self._running = True
        logger.info("Starting Trading MVP...")

        # Start ingestion pipeline
        if self.ingestion:
            await self.ingestion.start()
            logger.info("Ingestion started")

        logger.info("Trading MVP started")

    async def stop(self) -> None:
        """Stop all components."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping Trading MVP...")

        # Stop ingestion
        if self.ingestion:
            await self.ingestion.stop()

        # Close fundamental engine
        if self.fundamental_engine:
            await self.fundamental_engine.close()

        # Close journal
        if self.journal:
            self.journal.close()

        logger.info("Trading MVP stopped")

    async def run_api(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        """Run REST API server."""
        if not self.store or not self.audit_logger:
            raise RuntimeError("Must call initialize() before run_api()")

        app = create_app(store=self.store, audit_logger=self.audit_logger)

        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    async def run_full(self) -> None:
        """Run full application (ingestion + API)."""
        await self.initialize()
        await self.start()

        # Run API in same event loop
        import uvicorn
        from .api import create_app

        app = create_app(store=self.store, audit_logger=self.audit_logger)

        config = uvicorn.Config(
            app,
            host=self.app_config.api.host,
            port=self.app_config.api.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        try:
            await server.serve()
        finally:
            await self.stop()


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Trading MVP")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--api-only", action="store_true", help="Run API only")
    parser.add_argument("--host", default="127.0.0.1", help="API host")
    parser.add_argument("--port", type=int, default=8080, help="API port")
    args = parser.parse_args()

    app = TradingMVP(config_path=args.config)

    if args.api_only:
        # API only mode - needs initialized store
        await app.initialize()
        await app.run_api(host=args.host, port=args.port)
    else:
        await app.run_full()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        sys.exit(0)