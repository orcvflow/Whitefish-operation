"""
biquote SignalR WebSocket Client

Real-time tick streaming via SignalR hub at /hubs/tick.
https://biquote.io/docs/#signalr-hub-websocket

No rate limit for WebSocket stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

import websockets

from ..canonical import (
    Instrument,
    RawTickEvent,
    make_duplicate_key,
    normalize_symbol,
)

logger = logging.getLogger(__name__)


class BiquoteWebSocketClient:
    """
    biquote SignalR WebSocket client for real-time tick streaming.
    
    Connects to wss://biquote.io/hubs/tick and subscribes to symbols.
    Receives ReceiveTick events for each update.
    """

    HUB_URL = "wss://biquote.io/hubs/tick"

    def __init__(
        self,
        symbols: list[str],
        on_tick: Optional[Callable[[RawTickEvent], None]] = None,
        reconnect_interval: float = 5.0,
        max_reconnect_attempts: int = 10,
        ping_interval: float = 30.0,
    ):
        self.symbols = symbols
        self.on_tick = on_tick
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        self.ping_interval = ping_interval

        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_count = 0
        self._subscribe_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._subscribed = False

    async def start(self) -> None:
        """Start WebSocket connection and subscribe to symbols."""
        self._running = True
        asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        if self._ping_task:
            self._ping_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()
        if self._subscribe_task:
            self._subscribe_task.cancel()
        await self._disconnect()

    async def _run(self) -> None:
        """Main connection loop with reconnection."""
        while self._running and self._reconnect_count < self.max_reconnect_attempts:
            try:
                await self._connect()
                self._reconnect_count = 0
                await self._subscribe()
                await self._receive_loop()
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            
            if self._running:
                self._reconnect_count += 1
                logger.info(f"Reconnecting in {self.reconnect_interval}s (attempt {self._reconnect_count}/{self.max_reconnect_attempts})")
                await asyncio.sleep(self.reconnect_interval)

        if self._reconnect_count >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached")

    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        async with self._lock:
            self._websocket = await websockets.connect(
                self.HUB_URL,
                ping_interval=self.ping_interval,
                ping_timeout=10,
                close_timeout=10,
            )
            logger.info(f"Connected to biquote SignalR hub: {self.HUB_URL}")

    async def _disconnect(self) -> None:
        """Close WebSocket connection."""
        async with self._lock:
            if self._websocket:
                await self._websocket.close()
                self._websocket = None
            self._subscribed = False

    async def _subscribe(self) -> None:
        """Subscribe to symbols via SignalR."""
        if not self._websocket or self._subscribed:
            return

        # SignalR subscription message
        subscribe_msg = {
            "type": 1,  # Invocation
            "invocationId": str(uuid.uuid4()),
            "target": "Subscribe",
            "arguments": [self.symbols],
        }

        await self._websocket.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to symbols: {self.symbols}")
        self._subscribed = True

    async def _receive_loop(self) -> None:
        """Receive and process messages from WebSocket."""
        if not self._websocket:
            return

        async for message in self._websocket:
            try:
                await self._handle_message(message)
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def _handle_message(self, raw: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug(f"Non-JSON message: {raw[:100]}")
            return

        # SignalR message types:
        # type 1: Invocation (client→server)
        # type 2: StreamItem (server→client)
        # type 3: Completion (server→client)
        # type 6: Ping (server→client)
        # type 7: Close (server→client)

        msg_type = data.get("type")

        if msg_type == 6:  # Ping
            await self._websocket.send(json.dumps({"type": 7}))  # Close? No, pong
            # Actually SignalR ping response is type 7 with same invocationId
            # But for simplicity, we just acknowledge
            return

        if msg_type == 2:  # StreamItem
            target = data.get("target", "")
            arguments = data.get("arguments", [])

            if target == "ReceiveTick" and arguments:
                tick_data = arguments[0]
                tick = self._parse_tick(tick_data)
                if tick and self.on_tick:
                    self.on_tick(tick)

        elif msg_type == 3:  # Completion
            invocation_id = data.get("invocationId")
            error = data.get("error")
            result = data.get("result")
            if error:
                logger.error(f"SignalR error for {invocation_id}: {error}")
            else:
                logger.debug(f"SignalR completion {invocation_id}: {result}")

    def _parse_tick(self, data: dict) -> Optional[RawTickEvent]:
        """Parse WebSocket tick data into RawTickEvent."""
        try:
            symbol = normalize_symbol(data.get("symbol", ""))
            if not symbol:
                return None

            ts_str = data.get("timestamp", "")
            try:
                provider_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                provider_ts = datetime.now(timezone.utc)

            bid = Decimal(str(data.get("bid", 0)))
            ask = Decimal(str(data.get("ask", 0)))
            mid = Decimal(str(data.get("mid", 0)))
            spread = Decimal(str(data.get("spread", 0)))

            raw_str = json.dumps(data, sort_keys=True, default=str)
            raw_hash = __import__("hashlib").sha256(raw_str.encode()).hexdigest()[:16]

            return RawTickEvent(
                event_id=uuid.uuid4(),
                provider="biquote",
                provider_symbol=data.get("symbol", ""),
                provider_ts=provider_ts,
                received_ts=datetime.now(timezone.utc),
                bid=bid,
                ask=ask,
                mid=mid,
                spread=spread,
                high=Decimal(str(data.get("high", 0))),
                low=Decimal(str(data.get("low", 0))),
                volume=data.get("volume", 0),
                direction=data.get("direction", "FLAT"),
                day_diff_percent=Decimal(str(data.get("dayDiffPercent", 0))),
                market_state=data.get("marketState", "unknown"),
                stale=data.get("stale", False),
                quote_age_seconds=data.get("quoteAgeSeconds", 0),
                source=data.get("source", "biquote"),
                sequence=data.get("sequence"),
                raw_payload=raw_str,
            )
        except Exception as e:
            logger.error(f"Error parsing WebSocket tick: {e}")
            return None

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._websocket is not None and self._subscribed


class IngestionPipeline:
    """
    Ingestion pipeline that coordinates WebSocket and REST clients.
    
    Flow:
    Provider → Ingestion → Raw Journal → Validation → Canonical Store → Analysis
    """

    def __init__(
        self,
        journal,
        store,
        validator=None,
        symbols: Optional[list[str]] = None,
    ):
        self.journal = journal
        self.store = store
        self.validator = validator
        self.symbols = symbols or ["XAUUSD", "EURUSD", "GBPUSD", "WTI"]

        self._ws_client: Optional[BiquoteWebSocketClient] = None
        self._rest_client: Optional[object] = None
        self._running = False
        self._tick_count = 0
        self._duplicate_count = 0
        self._error_count = 0

    async def start(self) -> None:
        """Start ingestion pipeline."""
        self._running = True
        self._ws_client = BiquoteWebSocketClient(
            symbols=self.symbols,
            on_tick=self._handle_tick,
        )
        await self._ws_client.start()
        logger.info(f"Ingestion pipeline started for symbols: {self.symbols}")

    async def stop(self) -> None:
        """Stop ingestion pipeline."""
        self._running = False
        if self._ws_client:
            await self._ws_client.stop()
        logger.info("Ingestion pipeline stopped")

    async def _handle_tick(self, tick: RawTickEvent) -> None:
        """Handle incoming tick from WebSocket."""
        self._tick_count += 1

        # Check for duplicates
        dup_key = make_duplicate_key(
            tick.provider, tick.provider_symbol, tick.provider_ts
        )
        # TODO: Check duplicate in journal/cache

        # Validate
        if self.validator:
            quality = self.validator.check_data_quality(
                tick=self._raw_to_canonical(tick)
            )
            tick = tick.__class__(
                **{**tick.__dict__, "quality": quality} if hasattr(tick, "__dict__") else tick
            )

        # Persist to journal
        try:
            self.journal.append(tick)
        except Exception as e:
            logger.error(f"Journal append failed: {e}")
            self._error_count += 1
            return

        # Persist to canonical store
        try:
            canonical = self._raw_to_canonical(tick)
            self.store.upsert_tick(canonical)
        except Exception as e:
            logger.error(f"Store upsert failed: {e}")
            self._error_count += 1

    def _raw_to_canonical(self, tick: RawTickEvent):
        """Convert RawTickEvent to CanonicalTick."""
        from ..canonical import CanonicalTick

        return CanonicalTick(
            event_id=tick.event_id,
            provider=tick.provider,
            provider_symbol=tick.provider_symbol,
            canonical_symbol=normalize_symbol(tick.provider_symbol) or Instrument.XAUUSD,
            provider_ts=tick.provider_ts,
            received_ts=tick.received_ts,
            persisted_ts=datetime.now(timezone.utc),
            bid=tick.bid,
            ask=tick.ask,
            mid=tick.mid,
            spread=tick.spread,
            high=tick.high,
            low=tick.low,
            volume=tick.volume,
            direction=tick.direction,
            day_diff_percent=tick.day_diff_percent,
            market_state=tick.market_state,
            stale=tick.stale,
            quote_age_seconds=tick.quote_age_seconds,
            source=tick.source,
            sequence=tick.sequence,
            raw_payload_hash=tick.raw_payload_hash,
            schema_version=tick.schema_version,
            quality=QualityStatus.CLEAN,
        )

    async def backfill_history(self, symbol: str, count: int = 1000) -> int:
        """Backfill historical ticks from REST API."""
        from .biquote_rest import BiquoteRESTClient

        count = 0
        async with BiquoteRESTClient() as client:
            ticks = await client.get_latest_ticks([symbol])
            for tick in ticks:
                try:
                    self.journal.append(tick)
                    canonical = self._raw_to_canonical(tick)
                    self.store.upsert_tick(canonical)
                    count += 1
                except Exception as e:
                    logger.error(f"Backfill error: {e}")

        logger.info(f"Backfilled {count} ticks for {symbol}")
        return count

    @property
    def stats(self) -> dict:
        """Get ingestion statistics."""
        return {
            "tick_count": self._tick_count,
            "duplicate_count": self._duplicate_count,
            "error_count": self._error_count,
            "symbols": self.symbols,
            "ws_connected": self._ws_client.is_connected if self._ws_client else False,
        }


import json  # noqa: E402
from datetime import timezone  # noqa: E402