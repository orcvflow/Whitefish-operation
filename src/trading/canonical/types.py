"""
External API Type Definitions (TypedDict)

These define the shape of data coming from external providers.
Used for validation and documentation.
"""

from __future__ import annotations
from typing import TypedDict, NotRequired, Literal
from datetime import datetime
from decimal import Decimal


# =============================================================================
# biquote API Types
# =============================================================================

class BiquoteTick(TypedDict):
    """biquote /api/{symbol} response."""
    symbol: str
    description: str
    bid: float
    ask: float
    mid: float
    spread: float
    last: float
    volume: int
    high: float
    low: float
    direction: Literal["UP", "DOWN", "FLAT"]
    dayDiffPercent: float
    timestamp: str  # ISO 8601
    time: str       # MetaTrader format
    source: str
    marketState: Literal["open", "closed"]
    stale: bool
    quoteAgeSeconds: int
    lastQuoteAt: str  # ISO 8601


class BiquoteLatestResponse(TypedDict):
    """biquote /api/latest response - list of ticks."""
    # Actually returns a list of BiquoteTick


class BiquoteHistoryResponse(TypedDict):
    """biquote /api/{symbol}/history response."""
    # Returns list of BiquoteTick


class BiquoteSymbol(TypedDict):
    """biquote /api/symbols item."""
    name: str
    description: str
    type: Literal["Forex", "Stock", "Crypto", "Index", "Commodity"]
    exchange: str
    source: Literal["MT5", "MATRIKS"]


class BiquoteOHLCBar(TypedDict):
    """biquote OHLC bar."""
    openTime: str       # ISO 8601
    open: float
    high: float
    low: float
    close: float
    volume: int
    tickVolume: int
    isOpen: bool


class BiquoteOHLCResponse(TypedDict):
    """biquote /api/{symbol}/ohlc response."""
    symbol: str
    interval: str
    bars: list[BiquoteOHLCBar]


class BiquoteNewsItem(TypedDict):
    """biquote /api/news/market item."""
    title: str
    description: str
    url: str
    publisher: str
    publishedDate: str  # ISO 8601
    imageUrl: str | None
    category: str | None
    language: str
    country: str


class BiquoteCalendarEvent(TypedDict):
    """biquote /api/calendar item."""
    id: str
    eventId: str
    time: str                    # ISO 8601
    period: str                  # ISO 8601
    countryCode: str
    currency: str
    name: str
    importance: Literal["low", "medium", "high"]
    type: Literal["event", "indicator", "holiday"]
    sector: str
    unit: str
    multiplier: str
    digits: int
    actual: float | int | None
    forecast: float | int | str | None
    previous: float | int | str | None
    revisedPrevious: float | int | str | None
    revision: int
    timeMode: Literal["exact", "date", "notime", "tentative"]
    sourceUrl: str
    source: Literal["mql5", "other"]


class BiquoteCalendarCountries(TypedDict):
    """biquote /api/calendar/countries item."""
    code: str
    name: str
    currency: str


# =============================================================================
# ShowMyTrades Calendar Types
# =============================================================================

class SMTCalendarEvent(TypedDict):
    """showmytrades calendar event."""
    ts: int                      # Unix timestamp
    time: str                    # ISO 8601
    currency: str
    impact: Literal["high", "medium", "low", "holiday"]
    title: str
    forecast: str
    previous: str
    actual: str


class SMTCalendarResponse(TypedDict):
    """showmytrades calendar response."""
    server_time: int
    server_time_iso: str
    generated_at: str
    from_ts: str
    to_ts: str
    count: int
    events: list[SMTCalendarEvent]
    attribution: dict
    docs: str


# =============================================================================
# fin-data MCP Types (subset)
# =============================================================================

class FinDataYahooQuote(TypedDict):
    """fin-data yahoo_quote response."""
    currency: str
    symbol: str
    exchangeName: str
    fullExchangeName: str
    instrumentType: str
    regularMarketPrice: float
    regularMarketChangePercent: float
    fiftyTwoWeekHigh: float
    fiftyTwoWeekLow: float
    regularMarketVolume: int
    longName: str
    shortName: str


class FinDataFXRates(TypedDict):
    """fin-data fx_latest_rates response."""
    amount: int
    base: str
    date: str
    rates: dict[str, float]


class FinDataCryptoTicker(TypedDict):
    """fin-data crypto_ticker response."""
    instType: str
    instId: str
    last: str
    lastSz: str
    askPx: str
    askSz: str
    bidPx: str
    bidSz: str
    open24h: str
    high24h: str
    low24h: str
    volCcy24h: str
    vol24h: str
    ts: str
    sodUtc0: str
    sodUtc8: str


# =============================================================================
# AI Model Types
# =============================================================================

class AIMessage(TypedDict):
    """OpenAI-compatible message."""
    role: Literal["system", "user", "assistant"]
    content: str


class AICompletionRequest(TypedDict):
    """AI completion request."""
    model: str
    messages: list[AIMessage]
    temperature: float
    top_p: float
    top_k: int
    max_tokens: int


class AICompletionResponse(TypedDict):
    """AI completion response."""
    id: str
    choices: list[dict]
    usage: dict


# =============================================================================
# Internal Raw Event Types (before canonicalization)
# =============================================================================

class RawTickEvent(TypedDict):
    """Raw tick event before canonicalization."""
    event_id: str
    provider: str
    provider_symbol: str
    provider_ts: str
    received_ts: str
    bid: float
    ask: float
    mid: float
    spread: float
    high: float
    low: float
    volume: int
    direction: str
    day_diff_percent: float
    market_state: str
    stale: bool
    quote_age_seconds: int
    source: str
    sequence: NotRequired[int | None]
    raw_payload: str


class RawOHLCEvent(TypedDict):
    """Raw OHLC event before canonicalization."""
    event_id: str
    provider: str
    provider_symbol: str
    timeframe: str
    open_time: str
    close_time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_volume: int
    is_open: bool
    source: str
    raw_payload: str


class RawNewsEvent(TypedDict):
    """Raw news event before canonicalization."""
    event_id: str
    provider: str
    title: str
    url: str
    publisher: str
    published_at: str
    received_at: str
    description: str
    image_url: str | None
    category: str | None
    language: str
    country: str
    instrument_tags: list[str]
    content_hash: str
    raw_payload: str


class RawCalendarEvent(TypedDict):
    """Raw calendar event before canonicalization."""
    event_id: str
    provider: str
    ts: int
    time: str
    period: str
    country_code: str
    currency: str
    name: str
    impact: str
    type: str
    sector: str
    unit: str
    multiplier: str
    digits: int
    actual: float | int | str | None
    forecast: float | int | str | None
    previous: float | int | str | None
    revised_previous: float | int | str | None
    revision: int
    time_mode: str
    source_url: str
    raw_payload: str