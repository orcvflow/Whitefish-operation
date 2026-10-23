"""
biquote REST API Client

Real-time market data API for Forex, Crypto, Metals and Index CFDs.
https://biquote.io/docs/

No API key required. 15,000 requests per minute per IP.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx

from ..canonical import (
    Instrument,
    RawTickEvent,
    RawOHLCEvent,
    RawNewsEvent,
    RawCalendarEvent,
    QualityStatus,
    Timeframe,
    BiquoteTick,
    BiquoteOHLCBar,
    BiquoteNewsItem,
    BiquoteCalendarEvent,
    make_duplicate_key,
    normalize_symbol,
    round_to_precision,
    check_data_quality,
    validate_utc_timestamp,
)

logger = logging.getLogger(__name__)


class BiquoteRESTClient:
    """
    biquote REST API client for market data, news, calendar, and OHLC.
    
    Endpoints:
    - GET /api/{symbol} - Latest tick
    - GET /api/latest?symbols=... - Multiple symbols
    - GET /api/{symbol}/history?count=50 - Historical ticks
    - GET /api/{symbol}/ohlc?interval=1h&limit=100 - OHLC candles
    - GET /api/news/market - Market news
    - GET /api/calendar - Economic calendar
    - GET /api/symbols - Symbol metadata
    """

    BASE_URL = "https://biquote.io"

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        rate_limit_per_min: int = 15000,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limit_per_min = rate_limit_per_min
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_lock = asyncio.Lock()
        self._request_times: list[float] = []

    async def __aenter__(self) -> "BiquoteRESTClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers={"Accept": "application/json", "User-Agent": "trading-mvp/0.1.0"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"Accept": "application/json", "User-Agent": "trading-mvp/0.1.0"},
            )
        return self._client

    async def _rate_limit(self) -> None:
        """Enforce rate limiting."""
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            # Remove timestamps older than 1 minute
            self._request_times = [t for t in self._request_times if now - t < 60.0]

            if len(self._request_times) >= self.rate_limit_per_min:
                sleep_time = 60.0 - (now - self._request_times[0])
                if sleep_time > 0:
                    logger.warning(f"Rate limit reached, sleeping {sleep_time:.1f}s")
                    await asyncio.sleep(sleep_time)

            self._request_times.append(now)

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make GET request with rate limiting and retry."""
        client = await self._ensure_client()
        url = f"{self.BASE_URL}{path}"

        await self._rate_limit()

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 429:
                    retry_after = int(e.response.headers.get("Retry-After", "60"))
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                elif e.response.status_code == 404:
                    logger.warning(f"404 Not Found: {url}")
                    return {}
                else:
                    logger.error(f"HTTP error {e.response.status_code}: {url}")
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                last_error = e
                await asyncio.sleep(2 ** attempt)

        raise last_error or Exception("Max retries exceeded")

    # =============================================================================
    # Tick Data
    # =============================================================================

    async def get_latest_tick(self, symbol: str) -> Optional[RawTickEvent]:
        """Get latest tick for a single symbol."""
        data = await self._get(f"/api/{symbol}")
        if not data:
            return None

        return self._parse_tick(data)

    async def get_latest_ticks(self, symbols: list[str]) -> list[RawTickEvent]:
        """Get latest ticks for multiple symbols in one request."""
        params = [("symbols", s) for s in symbols]
        data = await self._get("/api/latest", params=params)

        if isinstance(data, list):
            return [self._parse_tick(item) for item in data if item]
        elif isinstance(data, dict) and "ticks" in data:
            return [self._parse_tick(item) for item in data["ticks"] if item]
        else:
            return [self._parse_tick(data)] if data else []

    def _parse_tick(self, data: BiquoteTick) -> Optional[RawTickEvent]:
        """Parse biquote tick response into RawTickEvent."""
        try:
            symbol = normalize_symbol(data.get("symbol", ""))
            if not symbol:
                logger.warning(f"Unknown symbol: {data.get('symbol')}")
                return None

            # Parse timestamp
            ts_str = data.get("timestamp", "")
            try:
                provider_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                provider_ts = datetime.now(timezone.utc)

            # Validate UTC
            utc_issues = validate_utc_timestamp(provider_ts, "provider_ts")
            if utc_issues:
                logger.warning(f"Timestamp issues for {symbol}: {utc_issues}")

            # Round prices to instrument precision
            # We need precision from config - for now use default
            precision = 5  # Default forex precision

            bid = Decimal(str(data.get("bid", 0)))
            ask = Decimal(str(data.get("ask", 0)))
            mid = Decimal(str(data.get("mid", 0)))
            spread = Decimal(str(data.get("spread", 0)))

            # Raw payload hash
            raw_str = json.dumps(data, sort_keys=True, default=str)
            raw_hash = hashlib.sha256(raw_str.encode()).hexdigest()[:16]

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
                sequence=None,
                raw_payload=raw_str,
            )
        except Exception as e:
            logger.error(f"Error parsing tick: {e}, data: {data}")
            return None


# Import json at module level
import json


class BiquoteOHLCRestClient:
    """biquote OHLC candle REST client."""

    BASE_URL = "https://biquote.io"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "BiquoteOHLCRestClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def get_ohlc_bars(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
    ) -> list[RawOHLCEvent]:
        """Get OHLC bars for a symbol."""
        params: dict = {"interval": interval, "limit": limit}
        if from_ts:
            params["from"] = from_ts.isoformat()
        if to_ts:
            params["to"] = to_ts.isoformat()

        data = await self._get(f"/api/{symbol}/ohlc", params=params)
        if not data:
            return []

        bars = data.get("bars", [])
        results = []
        for bar_data in bars:
            try:
                event = self._parse_ohlc(bar_data, symbol, interval)
                if event:
                    results.append(event)
            except Exception as e:
                logger.error(f"Error parsing OHLC bar: {e}")
        return results

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        client = await self._ensure_client()
        url = f"{self.BASE_URL}{path}"
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"Accept": "application/json"},
            )
        return self._client

    def _parse_ohlc(self, data: BiquoteOHLCBar, symbol: str, interval: str) -> Optional[RawOHLCEvent]:
        """Parse biquote OHLC bar into RawOHLCEvent."""
        try:
            inst = normalize_symbol(symbol)
            if not inst:
                return None

            open_time = datetime.fromisoformat(data["openTime"].replace("Z", "+00:00"))
            close_time = datetime.fromisoformat(data.get("closeTime", data["openTime"]).replace("Z", "+00:00"))

            raw_str = json.dumps(data, sort_keys=True, default=str)
            raw_hash = hashlib.sha256(raw_str.encode()).hexdigest()[:16]

            return RawOHLCEvent(
                event_id=uuid.uuid4(),
                provider="biquote",
                provider_symbol=symbol,
                provider_ts=open_time,
                received_ts=datetime.now(timezone.utc),
                timeframe=Timeframe(interval),
                open_time=open_time,
                close_time=close_time,
                open=Decimal(str(data["open"])),
                high=Decimal(str(data["high"])),
                low=Decimal(str(data["low"])),
                close=Decimal(str(data["close"])),
                volume=data.get("volume", 0),
                tick_volume=data.get("tickVolume", 0),
                is_open=data.get("isOpen", False),
                source=data.get("source", "biquote"),
                raw_payload=raw_str,
            )
        except Exception as e:
            logger.error(f"Error parsing OHLC: {e}")
            return None


class BiquoteNewsClient:
    """biquote news REST client."""

    BASE_URL = "https://biquote.io"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "BiquoteNewsClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def get_market_news(
        self,
        symbol: Optional[str] = None,
        language: str = "en",
        country: str = "US",
        max_results: int = 10,
    ) -> list[RawNewsEvent]:
        """Get market news from biquote."""
        params: dict = {"language": language, "country": country, "maxResults": max_results}
        if symbol:
            params["symbol"] = symbol

        data = await self._get("/api/news/market", params=params)
        if not data:
            return []

        results = []
        for item in data:
            try:
                event = self._parse_news(item)
                if event:
                    results.append(event)
            except Exception as e:
                logger.error(f"Error parsing news: {e}")
        return results

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        client = await self._ensure_client()
        url = f"{self.BASE_URL}{path}"
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"Accept": "application/json"},
            )
        return self._client

    def _parse_news(self, data: BiquoteNewsItem) -> Optional[RawNewsEvent]:
        """Parse biquote news item into RawNewsEvent."""
        try:
            published_at = datetime.fromisoformat(data["publishedDate"].replace("Z", "+00:00"))

            raw_str = json.dumps(data, sort_keys=True, default=str)
            content_hash = hashlib.sha256(f"{data['title']}{data['url']}".encode()).hexdigest()[:16]

            # Determine relevant instruments from title/content
            instrument_tags = self._detect_instruments(data.get("title", "") + " " + data.get("description", ""))

            return RawNewsEvent(
                event_id=uuid.uuid4(),
                provider="biquote",
                provider_symbol="NEWS",
                provider_ts=published_at,
                received_ts=datetime.now(timezone.utc),
                title=data["title"],
                url=data["url"],
                publisher=data.get("publisher", "biquote"),
                published_at=published_at,
                description=data.get("description", ""),
                image_url=data.get("imageUrl"),
                category=data.get("category"),
                language=data.get("language", "en"),
                country=data.get("country", "US"),
                instrument_tags=instrument_tags,
                content_hash=content_hash,
                raw_payload=raw_str,
            )
        except Exception as e:
            logger.error(f"Error parsing news: {e}")
            return None

    def _detect_instruments(self, text: str) -> list:
        """Detect which instruments are mentioned in text."""
        text_upper = text.upper()
        tags = []

        from ..canonical import Instrument
        for inst in Instrument:
            if inst.value in text_upper:
                tags.append(inst)

        # Also check for currency pairs
        for currency in ["XAU", "EUR", "GBP", "USD", "WTI", "OIL", "GOLD"]:
            if currency in text_upper:
                pass  # Already covered by instrument check

        return tags if tags else [Instrument.XAUUSD, Instrument.EURUSD]  # Default to all


class BiquoteCalendarClient:
    """biquote economic calendar REST client."""

    BASE_URL = "https://biquote.io"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "BiquoteCalendarClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def get_calendar(
        self,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
        countries: Optional[str] = None,
        importance: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> list[RawCalendarEvent]:
        """Get economic calendar events from biquote."""
        params: dict = {"limit": limit}
        if from_ts:
            params["from"] = from_ts.isoformat()
        if to_ts:
            params["to"] = to_ts.isoformat()
        if countries:
            params["countries"] = countries
        if importance:
            params["importance"] = importance
        if event_type:
            params["type"] = event_type

        data = await self._get("/api/calendar", params=params)
        if not data:
            return []

        results = []
        for item in data:
            try:
                event = self._parse_calendar(item)
                if event:
                    results.append(event)
            except Exception as e:
                logger.error(f"Error parsing calendar: {e}")
        return results

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        client = await self._ensure_client()
        url = f"{self.BASE_URL}{path}"
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"Accept": "application/json"},
            )
        return self._client

    def _parse_calendar(self, data: BiquoteCalendarEvent) -> Optional[RawCalendarEvent]:
        """Parse biquote calendar event into RawCalendarEvent."""
        try:
            from ..canonical import NewsImpact

            time_str = data.get("time", "")
            period_str = data.get("period", "")

            try:
                event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                event_time = datetime.now(timezone.utc)

            try:
                event_period = datetime.fromisoformat(period_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                event_period = event_time

            impact_str = data.get("importance", "low")
            try:
                impact = NewsImpact(impact_str)
            except ValueError:
                impact = NewsImpact.LOW

            actual = None
            if data.get("actual") is not None:
                try:
                    actual = Decimal(str(data["actual"]))
                except Exception:
                    pass

            forecast = None
            if data.get("forecast") is not None:
                try:
                    forecast = Decimal(str(data["forecast"]))
                except Exception:
                    pass

            previous = None
            if data.get("previous") is not None:
                try:
                    previous = Decimal(str(data["previous"]))
                except Exception:
                    pass

            raw_str = json.dumps(data, sort_keys=True, default=str)

            return RawCalendarEvent(
                event_id=uuid.uuid4(),
                provider="biquote",
                provider_symbol="CALENDAR",
                provider_ts=event_time,
                received_ts=datetime.now(timezone.utc),
                ts=int(event_time.timestamp()),
                time=event_time,
                period=event_period,
                country_code=data.get("countryCode", ""),
                currency=data.get("currency", ""),
                name=data.get("name", ""),
                impact=impact,
                event_type=data.get("type", "event"),
                sector=data.get("sector", ""),
                unit=data.get("unit", ""),
                multiplier=data.get("multiplier", ""),
                digits=data.get("digits", 0),
                actual=actual,
                forecast=forecast,
                previous=previous,
                revised_previous=None,
                revision=data.get("revision", 1),
                time_mode=data.get("timeMode", "exact"),
                source_url=data.get("sourceUrl", ""),
                raw_payload=raw_str,
            )
        except Exception as e:
            logger.error(f"Error parsing calendar: {e}")
            return None