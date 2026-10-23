"""
MCP Adapter for News and Calendar Data

Uses existing MCP servers:
- fin-data (83 tools) - Market data, news, macro, SEC filings, congress trading
- searxng (4 tools) - Web search for fundamental research

Preserves existing MCP servers - does not create new ones.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from ..canonical import (
    Instrument,
    RawNewsEvent,
    RawCalendarEvent,
    NewsImpact,
    normalize_symbol,
)

logger = logging.getLogger(__name__)


class MCPNewsAdapter:
    """
    News adapter using existing MCP servers.
    
    Uses:
    - fin-data: sec_company_filings, sec_full_text_search, yahoo_search
    - searxng: web search, url read for breaking news verification
    """

    def __init__(self, mcp_client=None):
        """
        Initialize with MCP client.
        
        mcp_client should have methods like:
        - call_tool(server_name, tool_name, arguments)
        or be an object with similar interface
        """
        self.mcp_client = mcp_client
        self._fin_data_available = False
        self._searxng_available = False

    async def check_availability(self) -> dict[str, bool]:
        """Check which MCP servers are available."""
        result = {"fin-data": False, "searxng": False}

        if self.mcp_client:
            try:
                # Try to list tools from fin-data
                tools = await self.mcp_client.list_tools("fin-data")
                result["fin-data"] = len(tools) > 0
            except Exception as e:
                logger.debug(f"fin-data not available: {e}")

            try:
                tools = await self.mcp_client.list_tools("searxng")
                result["searxng"] = len(tools) > 0
            except Exception as e:
                logger.debug(f"searxng not available: {e}")

        self._fin_data_available = result["fin-data"]
        self._searxng_available = result["searxng"]
        return result

    async def get_sec_filings(
        self,
        ticker: str,
        limit: int = 10,
    ) -> list[RawNewsEvent]:
        """Get SEC EDGAR filings via fin-data MCP."""
        if not self._fin_data_available or not self.mcp_client:
            return []

        try:
            result = await self.mcp_client.call_tool(
                "fin-data",
                "sec_company_filings",
                {"ticker": ticker, "limit": limit},
            )
            return self._parse_sec_filings(result, ticker)
        except Exception as e:
            logger.error(f"SEC filings error for {ticker}: {e}")
            return []

    def _parse_sec_filings(
        self,
        result: dict,
        ticker: str,
    ) -> list[RawNewsEvent]:
        """Parse SEC filings into RawNewsEvent list."""
        events = []

        try:
            filings = result.get("filings", [])
            if isinstance(filings, dict):
                filings = filings.get("recent", [])

            for filing in filings:
                try:
                    content_hash = hashlib.sha256(
                        f"{filing.get('form', '')}{filing.get('accessionNumber', '')}".encode()
                    ).hexdigest()[:16]

                    published_str = filing.get("filingDate", "")
                    try:
                        published_at = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        published_at = datetime.now(timezone.utc)

                    events.append(RawNewsEvent(
                        event_id=uuid.uuid4(),
                        provider="fin-data/sec",
                        provider_symbol=ticker,
                        provider_ts=published_at,
                        received_ts=datetime.now(timezone.utc),
                        title=f"{ticker} {filing.get('form', 'Filing')}",
                        url=f"https://www.sec.gov/Archives/edgar/data/{filing.get('cik', '')}/{filing.get('accessionNumber', '').replace('-', '')}/",
                        publisher="SEC EDGAR",
                        published_at=published_at,
                        description=filing.get("form", ""),
                        image_url=None,
                        category="filing",
                        language="en",
                        country="US",
                        instrument_tags=[normalize_symbol(ticker)] if normalize_symbol(ticker) else [],
                        content_hash=content_hash,
                        raw_payload=json.dumps(filing, sort_keys=True, default=str),
                    ))
                except Exception as e:
                    logger.error(f"Error parsing SEC filing: {e}")
        except Exception as e:
            logger.error(f"Error parsing SEC filings: {e}")

        return events

    async def search_web(
        self,
        query: str,
        limit: int = 10,
    ) -> list[RawNewsEvent]:
        """Search web for breaking news via searxng MCP."""
        if not self._searxng_available or not self.mcp_client:
            return []

        try:
            result = await self.mcp_client.call_tool(
                "searxng",
                "searxng_search_suggestions",
                {"query": query, "limit": limit},
            )
            return self._parse_search_results(result, query)
        except Exception as e:
            logger.error(f"Web search error for '{query}': {e}")
            return []

    def _parse_search_results(
        self,
        result: dict,
        query: str,
    ) -> list[RawNewsEvent]:
        """Parse search results into RawNewsEvent list."""
        events = []

        try:
            suggestions = result.get("suggestions", [])
            for suggestion in suggestions:
                try:
                    content_hash = hashlib.sha256(suggestion.encode()).hexdigest()[:16]
                    now = datetime.now(timezone.utc)

                    events.append(RawNewsEvent(
                        event_id=uuid.uuid4(),
                        provider="searxng",
                        provider_symbol="SEARCH",
                        provider_ts=now,
                        received_ts=now,
                        title=suggestion,
                        url="",
                        publisher="searxng",
                        published_at=now,
                        description=f"Search suggestion for: {query}",
                        image_url=None,
                        category="search",
                        language="en",
                        country="US",
                        instrument_tags=self._detect_instruments(suggestion),
                        content_hash=content_hash,
                        raw_payload=json.dumps({"query": query, "suggestion": suggestion}, sort_keys=True),
                    ))
                except Exception as e:
                    logger.error(f"Error parsing search suggestion: {e}")
        except Exception as e:
            logger.error(f"Error parsing search results: {e}")

        return events

    def _detect_instruments(self, text: str) -> list:
        """Detect instruments mentioned in text."""
        text_upper = text.upper()
        tags = []

        for inst in Instrument:
            if inst.value in text_upper:
                tags.append(inst)

        return tags if tags else []


class MCPCalendarAdapter:
    """
    Calendar adapter using existing MCP servers.
    
    Uses:
    - fin-data: worldbank_indicator, oecd_data, dbnomics_series for macro data
    - biquote REST (already implemented) for economic calendar
    """

    def __init__(self, mcp_client=None):
        self.mcp_client = mcp_client
        self._fin_data_available = False

    async def check_availability(self) -> dict[str, bool]:
        """Check MCP server availability."""
        result = {"fin-data": False}

        if self.mcp_client:
            try:
                tools = await self.mcp_client.list_tools("fin-data")
                result["fin-data"] = len(tools) > 0
            except Exception as e:
                logger.debug(f"fin-data not available: {e}")

        self._fin_data_available = result["fin-data"]
        return result

    async def get_macro_indicator(
        self,
        indicator: str,
        country: str = "US",
        date: str = "2020",
    ) -> Optional[dict]:
        """Get macro indicator from World Bank via fin-data MCP."""
        if not self._fin_data_available or not self.mcp_client:
            return None

        try:
            result = await self.mcp_client.call_tool(
                "fin-data",
                "worldbank_indicator",
                {
                    "country": country,
                    "indicator": indicator,
                    "date": date,
                },
            )
            return result
        except Exception as e:
            logger.error(f"World Bank indicator error: {e}")
            return None

    async def get_oecd_data(
        self,
        dataflow: str,
        key: str = "all",
        start_period: Optional[str] = None,
        end_period: Optional[str] = None,
    ) -> Optional[dict]:
        """Get OECD macro data via fin-data MCP."""
        if not self._fin_data_available or not self.mcp_client:
            return None

        try:
            params: dict = {"dataflow": dataflow, "key": key}
            if start_period:
                params["startPeriod"] = start_period
            if end_period:
                params["endPeriod"] = end_period

            result = await self.mcp_client.call_tool(
                "fin-data",
                "oecd_data",
                params,
            )
            return result
        except Exception as e:
            logger.error(f"OECD data error: {e}")
            return None

    async def get_dbnomics_series(
        self,
        provider: str,
        dataset: str,
        series: str,
    ) -> Optional[dict]:
        """Get DBnomics series data via fin-data MCP."""
        if not self._fin_data_available or not self.mcp_client:
            return None

        try:
            result = await self.mcp_client.call_tool(
                "fin-data",
                "dbnomics_series",
                {
                    "provider": provider,
                    "dataset": dataset,
                    "series": series,
                },
            )
            return result
        except Exception as e:
            logger.error(f"DBnomics series error: {e}")
            return None