# PLAN.md — Quantitative Trading MVP Implementation Plan

## Overview
Build a modular monolith Python system for fundamental-technical trading analysis on XAUUSD, EURUSD, GBPUSD, WTI.
**No live trading** — analysis, decision plans, simulation, and audit only.

---

## Phase 1: Foundation & Audit (Week 1)

### 1.1 Repository Audit
- [x] Inventory existing code, dependencies, MCP servers
- [x] Document fin-data MCP tools (83 tools, 15 providers)
- [x] Document searxng MCP tools (4 tools)
- [x] Verify Axle-Axiom Python client works
- [x] Backup OpenCode config
- [ ] Create AGENTS.md ✓ (done)
- [ ] Create PLAN.md ✓ (this file)

### 1.2 Official Documentation Verification
- [x] biquote.io/docs — REST + SignalR WS, no auth, 15k req/min
- [x] showmytrades.com/docs/api/economic-calendar — free, no key, 600 req/min
- [x] huggingface.co/inclusionAI/Ling-3.0-flash-Fin — 124B params, OpenRouter available
- [x] axle.axiommath.ai — Lean 4 verification via Python client
- [ ] Document exact API response schemas for each endpoint

### 1.3 Development Environment
- [ ] Set up Python 3.11+ venv with all deps
- [ ] Configure ruff, mypy, pytest, hypothesis
- [ ] Set up pre-commit hooks
- [ ] Create project structure

**Deliverable:** Working dev environment, verified docs, project structure

---

## Phase 2: Canonical Schemas & Validation (Week 1-2)

### 2.1 Type Definitions (`src/trading/canonical/`)
```python
# types.py — External API shapes (TypedDict)
# models.py — Internal DTOs (frozen dataclasses)
# validation.py — Schema validation, invariants
# enums.py — QualityStatus, DecisionStatus, Instrument, Timeframe
```

### 2.2 Required Schemas
| Schema | Key Fields | Invariants |
|--------|------------|------------|
| `RawTick` | event_id, provider, symbol, bid, ask, mid, spread, ts, raw_hash | bid ≤ mid ≤ ask |
| `CanonicalTick` | + received_ts, persisted_ts, sequence, quality | All timestamps UTC |
| `OHLCBar` | symbol, tf, open_time, close_time, O/H/L/C, volume, tick_vol, is_open, source, revision, quality | H ≥ max(O,C), L ≤ min(O,C), H ≥ L |
| `NewsItem` | id, title, url, publisher, published_at, received_at, content_hash, instrument_tags, source, quality | content_hash = SHA256(title+url) |
| `CalendarEvent` | id, ts, time, country, currency, name, impact, type, forecast, previous, actual, revised, source_url, source | actual=None until published |
| `AnalysisSnapshot` | snapshot_id, timestamp, symbol, tick_ref, news_refs, calendar_refs, technical_ref, quality | Immutable, versioned |
| `FundamentalThesis` | thesis, sources, causal_chain, per_instrument_impact, counter_args, invalidation_conditions, uncertainty | Structured, not free text |
| `TechnicalAnalysis` | h1_structure, m15_rsi, patterns, entry_trigger, sl, tp1, tp2, rr, spread, risk_checks | All Decimal |
| `Decision` | decision_id, snapshot_id, symbol, status, timestamp, entry/sl/tp1/tp2, risk_pct, rr, failed_gates, strategy_version, data_quality, axle_results | Audit-complete |

### 2.3 Validation Rules
- OHLC invariants enforced at construction
- Decimal precision per instrument
- Timestamp timezone awareness (UTC only)
- Duplicate detection: `(provider, provider_symbol, provider_ts)`
- Gap detection: sequence gaps, time gaps > expected interval

**Deliverable:** `src/trading/canonical/` with 100% test coverage

---

## Phase 3: Persistence Layer (Week 2)

### 3.1 Append-Only Journal (`src/trading/persistence/journal.py`)
```python
class EventJournal:
    """Append-only, crash-safe event log."""
    def append(self, event: RawEvent) -> None
    def replay(self, from_seq: int) -> Iterator[RawEvent]
    def get_last_sequence(self) -> int
```

- JSON Lines format, one event per line
- fsync on every append (configurable batch)
- Rotation by size/date
- SHA256 hash per line for integrity

### 3.2 SQLite WAL Store (`src/trading/persistence/store.py`)
```python
class CanonicalStore:
    """SQLite WAL for canonical data."""
    def upsert_tick(self, tick: CanonicalTick) -> None
    def upsert_ohlc(self, bar: OHLCBar) -> None
    def upsert_news(self, news: NewsItem) -> None
    def upsert_calendar(self, event: CalendarEvent) -> None
    def save_snapshot(self, snapshot: AnalysisSnapshot) -> None
    def save_decision(self, decision: Decision) -> None
    def get_ticks(self, symbol: str, from_ts: datetime, to_ts: datetime) -> list[CanonicalTick]
    def get_ohlc(self, symbol: str, tf: Timeframe, limit: int) -> list[OHLCBar]
```

- WAL mode, foreign keys ON
- Indexes on (symbol, timestamp)
- Migration system (versioned schema)
- Backup: `VACUUM INTO` + journal copy

### 3.3 Recovery & Replay
- `recover()` — replay journal from last checkpoint
- `verify_integrity()` — hash check, sequence continuity
- `backup()` / `restore()` — tested in CI

**Deliverable:** Persistence layer with crash recovery tests

---

## Phase 4: Market Data Ingestion (Week 2-3)

### 4.1 biquote Adapter (`src/trading/ingestion/biquote.py`)
```python
class BiquoteWSClient:
    """SignalR WebSocket client for real-time ticks."""
    async def connect(self) -> None
    async def subscribe(self, symbols: list[str]) -> None
    async def on_tick(self, callback: Callable[[RawTick], Awaitable[None]]) -> None
    async def handle_reconnect(self) -> None

class BiquoteRESTClient:
    """REST fallback for history, symbols, OHLC."""
    async def get_latest(self, symbols: list[str]) -> list[RawTick]
    async def get_history(self, symbol: str, count: int) -> list[RawTick]
    async def get_ohlc(self, symbol: str, interval: str, limit: int) -> list[RawOHLC]
    async def get_symbols(self) -> list[SymbolMeta]
```

- WebSocket primary, REST for backfill
- Auto-reconnect with exponential backoff
- Rate limit awareness (batch symbols)
- Provider metadata capture (source, timestamp)

### 4.2 Ingestion Pipeline (`src/trading/ingestion/pipeline.py`)
```python
class IngestionPipeline:
    def __init__(self, journal: EventJournal, store: CanonicalStore, validator: Validator):
        ...
    async def process_tick(self, raw: RawTick) -> CanonicalTick:
        # 1. Persist raw to journal
        # 2. Validate & canonicalize
        # 3. Duplicate check
        # 4. Gap detection
        # 5. Persist to store
        # 6. Return canonical tick
```

### 4.3 OHLC Builder (`src/trading/ingestion/ohlc_builder.py`)
- Local tick aggregation → M1, M5, M15, H1, H4, D1
- Compare with provider OHLC (biquote `/ohlc` endpoint)
- Log discrepancies, flag quality issues
- Only closed bars used for strategy

**Deliverable:** Real-time ingestion with WAL persistence, gap detection, OHLC building

---

## Phase 5: News & Calendar Ingestion (Week 3)

### 5.1 News Adapter (`src/trading/ingestion/news.py`)
- biquote `/api/news/market` (with symbol filter)
- biquote `/api/news/hn` (tech/finance)
- fin-data MCP: `sec_company_filings`, `sec_full_text_search`
- searxng MCP: targeted web search for breaking news
- Deduplication by `content_hash`
- Quality scoring: source reliability, recency, relevance

### 5.2 Calendar Adapter (`src/trading/ingestion/calendar.py`)
- biquote `/api/calendar` (54 countries, 90-day window)
- showmytrades.com `/api/public/calendar/v1` (9 majors, free, no key)
- Merge feeds, deduplicate by `event_id`
- Server time sync for accurate countdown
- Impact filtering: high/medium for blackout

### 5.3 Blackout Manager (`src/trading/risk/blackout.py`)
```python
class NewsBlackout:
    def is_blackout(self, symbol: str, now: datetime) -> tuple[bool, str | None]
    def get_upcoming_high_impact(self, hours: int) -> list[CalendarEvent]
```
- ±30 min around high-impact events
- Per-currency blackout for forex pairs
- XAUUSD: USD events + gold-specific
- WTI: energy inventories, OPEC, geopolitical

**Deliverable:** News + calendar ingestion with blackout logic

---

## Phase 6: Fundamental Analysis Engine (Week 3-4)

### 6.1 AI Adapter Framework (`src/trading/fundamental/adapters/`)
```python
class AIAdapter(Protocol):
    async def analyze(self, prompt: str, schema: type[BaseModel]) -> BaseModel
    async def close(self) -> None

class OpenRouterAdapter:
    """Ling-3.0-flash-Fin via OpenRouter."""
    ...

class LocalAdapter:
    """For testing with mocked responses."""
    ...
```

### 6.2 Snapshot Builder (`src/trading/fundamental/snapshot.py`)
```python
def build_fundamental_snapshot(
    symbol: str,
    news: list[NewsItem],
    calendar: list[CalendarEvent],
    technical: TechnicalAnalysis,
    market_state: MarketState
) -> FundamentalSnapshot:
    """Compact, immutable context for AI."""
```

### 6.3 Prompt Engineering (`src/trading/fundamental/prompts.py`)
Structured prompt requiring:
1. **Fact** — what happened, primary source
2. **Economic Variable** — which indicator changes
3. **Transmission Channel** → **Asset Impact** (per instrument)
4. **Counter-arguments** — why thesis could be wrong
5. **Invalidation Conditions** — what breaks the thesis
6. **Uncertainty Assessment** — proven/likely/speculative

### 6.4 Fundamental Engine (`src/trading/fundamental/engine.py`)
```python
class FundamentalEngine:
    async def analyze(self, snapshot: FundamentalSnapshot) -> FundamentalThesis:
        # 1. Filter relevant news/calendar
        # 2. Build prompt
        # 3. Call AI (with timeout, retry)
        # 4. Parse & validate response
        # 5. Store thesis + raw response
        # 6. Return structured thesis
```

**Deliverable:** AI-driven fundamental analysis with structured output

---

## Phase 7: Technical Analysis Engine (Week 4)

### 7.1 H1 Market Structure (`src/trading/technical/structure.py`)
```python
@dataclass(frozen=True)
class H1Structure:
    strong_highs: list[PriceLevel]
    strong_lows: list[PriceLevel]
    weak_highs: list[PriceLevel]
    weak_lows: list[PriceLevel]
    equal_highs: list[PriceLevel]
    equal_lows: list[PriceLevel]
    trend: TrendDirection
    top_zone: Zone
    bottom_zone: Zone
    middle_zone: Zone
    current_price: Decimal
    timestamp: datetime
```

- Swing detection: fractal-based, configurable lookback
- Strength classification: volume, test count, reaction
- Equal highs/lows: price tolerance = 0.5 * ATR
- Zones: dynamic, based on structure

### 7.2 Indicators (`src/trading/technical/indicators.py`)
```python
def rsi(closes: list[Decimal], period: int = 14) -> Decimal:
    """Wilder's smoothing, handles insufficient data."""
    ...

def atr(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int = 14) -> Decimal:
    ...
```

- Pure functions, no external deps
- Deterministic for same input
- Property tests with known vectors

### 7.3 Candle Patterns (`src/trading/technical/patterns.py`)
```python
@dataclass(frozen=True)
class CandlePattern:
    name: Literal["3-1", "1-1", "1-3", "3-3"]
    bullish: bool
    body_ratio: Decimal  # body / range
    upper_wick_ratio: Decimal
    lower_wick_ratio: Decimal
    confirmation_required: bool

def classify_candle(open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> CandlePattern | None:
    """Formal definitions from config."""
    ...
```

### 7.4 Entry/SL/TP Calculator (`src/trading/technical/levels.py`)
```python
def calculate_entry(signal_high: Decimal, signal_low: Decimal, direction: Direction, point_value: Decimal) -> Decimal:
    """Entry = signal_high + 30pts (BUY) or signal_low - 30pts (SELL)."""
    ...

def calculate_sl(signal_candle: OHLCBar, direction: Direction, buffer_pts: Decimal, liquidity_levels: list[Decimal]) -> Decimal:
    ...

def calculate_tp(structure: H1Structure, direction: Direction, entry: Decimal) -> tuple[Decimal, Decimal]:
    """TP1, TP2 from structure levels."""
    ...
```

### 7.5 Technical Engine (`src/trading/technical/engine.py`)
```python
class TechnicalEngine:
    def analyze(self, symbol: str, ohlc_h1: list[OHLCBar], ohlc_m15: list[OHLCBar]) -> TechnicalAnalysis:
        # 1. Build H1 structure (closed bars only)
        # 2. Calculate M15 RSI(14)
        # 3. Classify last closed M15 candle
        # 4. Determine entry trigger
        # 5. Calculate SL, TP1, TP2
        # 6. Compute RR
        # 7. Validate risk checks
```

**Deliverable:** Deterministic technical engine with formal definitions

---

## Phase 8: Risk Engine (Week 4)

### 8.1 Risk Rules (`src/trading/risk/rules.py`)
```python
@dataclass(frozen=True)
class RiskConfig:
    default_risk_pct: Decimal = Decimal("0.01")
    max_trade_risk_pct: Decimal = Decimal("0.02")
    daily_loss_limit_pct: Decimal = Decimal("0.03")
    max_trades_per_day: int = 3
    min_risk_reward: Decimal = Decimal("2.0")
    max_spread_pct: dict[str, Decimal] = field(default_factory=lambda: {
        "XAUUSD": Decimal("0.0005"),
        "EURUSD": Decimal("0.0002"),
        "GBPUSD": Decimal("0.0003"),
        "WTI":    Decimal("0.0010"),
    })
```

### 8.2 Risk Engine (`src/trading/risk/engine.py`)
```python
class RiskEngine:
    def check_pre_trade(self, ctx: RiskContext) -> RiskCheckResult:
        """All hard gates. Returns (passed, failed_gates)."""
        checks = [
            self._check_spread,
            self._check_data_quality,
            self._check_market_hours,
            self._check_blackout,
            self._check_rr,
            self._check_daily_limits,
            self._check_stale_data,
        ]
        ...
```

### 8.3 Position Sizing (`src/trading/risk/sizing.py`)
```python
def calculate_position_size(
    account_equity: Decimal,
    risk_pct: Decimal,
    entry: Decimal,
    sl: Decimal,
    contract_size: Decimal,
    tick_value: Decimal,
    account_currency: str,
    symbol: str
) -> Decimal:
    """Standard 1% risk position sizing."""
    ...
```

**Deliverable:** Hard risk gates, position sizing

---

## Phase 9: Decision Engine (Week 4-5)

### 9.1 Decision Logic (`src/trading/decision/engine.py`)
```python
@dataclass(frozen=True)
class DecisionContext:
    symbol: str
    snapshot: AnalysisSnapshot
    fundamental: FundamentalThesis
    technical: TechnicalAnalysis
    risk_check: RiskCheckResult
    strategy_version: str

class DecisionEngine:
    def decide(self, ctx: DecisionContext) -> Decision:
        # 1. If any hard gate failed → SKIP
        # 2. If fundamental + technical align + setup valid → OPEN
        # 3. If setup forming but trigger not hit → WAIT
        # 4. If conflict → SKIP (log conflict)
        # 5. Build Decision with full audit trail
```

### 9.2 Conflict Handling
- Fundamental bullish + technical bearish → explicit conflict in audit
- AI confidence < threshold → WAIT
- Data quality degraded → SKIP

**Deliverable:** Deterministic decision engine with full audit

---

## Phase 10: AXLE Integration (Week 5)

### 10.1 Verification Module (`src/trading/axle/verifier.py`)
```python
class AXLEVerifier:
    def __init__(self, api_key: str):
        self.client = AxleClient(api_key)

    async def verify_ohlc_invariants(self, bars: list[OHLCBar]) -> VerificationResult:
        """Lean 4 proof that H ≥ max(O,C), L ≤ min(O,C), H ≥ L."""
        ...

    async def verify_rsi_formula(self, closes: list[Decimal], period: int, expected: Decimal) -> VerificationResult:
        ...

    async def verify_rr_calc(self, entry: Decimal, sl: Decimal, tp: Decimal, expected_rr: Decimal) -> VerificationResult:
        ...

    async def verify_position_sizing(self, equity: Decimal, risk_pct: Decimal, entry: Decimal, sl: Decimal, expected_size: Decimal) -> VerificationResult:
        ...
```

### 10.2 Integration Points
- Call after technical analysis, before decision
- Log all verifications (pass/fail + Lean output)
- Non-blocking: if AXLE down, log warning, continue with Python tests

**Deliverable:** Mathematical verification of core calculations

---

## Phase 11: Audit & API (Week 5)

### 11.1 Audit Logger (`src/trading/audit/logger.py`)
```python
class AuditLogger:
    def log_decision(self, decision: Decision) -> None:
        """Append-only JSONL + SQLite index."""
        ...

    def get_decision(self, decision_id: str) -> Decision | None:
        ...

    def query_decisions(self, filters: DecisionFilters) -> list[Decision]:
        ...
```

### 11.2 REST API (`src/trading/api/server.py`)
```python
# FastAPI minimal endpoints
GET  /health                    → {status, uptime, components}
GET  /metrics                   → Prometheus text
GET  /market/{symbol}           → latest tick + quality
GET  /candles/{symbol}          → OHLC bars (tf, limit)
GET  /news                      → recent news (symbol, hours)
GET  /calendar                  → upcoming events (currencies, impact)
GET  /analysis/{symbol}         → latest fundamental + technical
GET  /decisions                 → recent decisions (limit, status)
GET  /decisions/{decision_id}   → full audit trail
```

- No auth (internal tool)
- Rate limited
- Structured error responses

**Deliverable:** Complete audit trail + monitoring API

---

## Phase 12: Testing & Validation (Week 5-6)

### 12.1 Unit Tests (continuous)
| Module | Key Tests |
|--------|-----------|
| `canonical` | All invariants, serialization, precision |
| `indicators` | RSI/ATR known vectors, edge cases |
| `structure` | Swing detection, zone boundaries |
| `patterns` | Pattern classification boundaries |
| `levels` | Entry/SL/TP math, RR calc |
| `risk` | All gate combinations |
| `decision` | All status paths, conflict cases |

### 12.2 Property Tests (`tests/property/`)
```python
@given(ohlc_bars())
def test_ohlc_invariants(bars):
    for bar in bars:
        assert bar.high >= max(bar.open, bar.close)
        assert bar.low <= min(bar.open, bar.close)
        assert bar.high >= bar.low

@given(ticks())
def test_deterministic_ohlc_builder(ticks):
    result1 = build_ohlc(ticks)
    result2 = build_ohlc(ticks)
    assert result1 == result2
```

### 12.3 Integration Tests (`tests/integration/`)
- Full ingestion → persistence → recovery
- News + calendar fetch → parse → store
- AI adapter timeout / invalid JSON / rate limit
- AXLE success / failure / timeout
- Decision engine with mocked components

### 12.4 Replay Tests (`tests/replay/`)
- Journal replay from checkpoint
- Historical backtest (no look-ahead)
- Gap injection → verify block
- Duplicate injection → verify idempotency

### 12.5 Backtest Framework (`tests/backtest/`)
```python
class BacktestEngine:
    def run(self, strategy: Strategy, data: HistoricalData, config: BacktestConfig) -> BacktestResult:
        # Event-driven, no look-ahead
        # Spread, commission, slippage models
        # Outputs: expectancy, max_dd, profit_factor, win_rate, trade_count
```

**Deliverable:** Full test suite passing in CI

---

## Phase 13: End-to-End Validation (Week 6)

### 13.1 Smoke Test
- Start all components
- Feed live data for 1 hour
- Verify: ticks persisted, OHLC built, decisions logged
- Check audit trail completeness

### 13.2 Latency Measurement
| Stage | Target |
|-------|--------|
| Provider → Receive | < 50ms |
| Receive → Persist | < 10ms |
| Persist → Process | < 100ms |
| News Fetch + Parse | < 5s |
| AI Request | < 30s |
| Decision Engine | < 10ms |
| **End-to-End** | **< 45s** (AI path) |

### 13.3 Recovery Test
- Kill process mid-ingestion
- Restart → verify replay completes
- Verify no duplicate decisions
- Verify no data loss

---

## Dependencies & Risks

| Dependency | Risk | Mitigation |
|------------|------|------------|
| biquote WS | Provider outage | REST fallback, stale quote detection |
| OpenRouter | Rate limit / cost | Local adapter for dev, caching |
| AXLE API | Latency / downtime | Non-blocking, Python fallback tests |
| fin-data MCP | Tool changes | Pin versions, integration tests |
| SQLite WAL | Corruption | Backup + integrity check on startup |

---

## Acceptance Criteria (from Master Prompt)

- [ ] Existing MCP servers preserved (fin-data, searxng, Axle)
- [ ] Price data persisted + replay on restart
- [ ] Data gaps detected + logged (not hidden)
- [ ] News stored with source + time metadata
- [ ] Fundamental analysis: causal chain + counter-arguments
- [ ] Technical indicators: deterministic Python
- [ ] Risk rules: AI-independent, hard gates
- [ ] Full audit trail per decision
- [ ] Math verified: Python tests + AXLE where applicable
- [ ] No unmeasured latency, undocumented limits, synthetic-as-real
- [ ] No broker orders sent

---

## Final Deliverables

1. **Source Code** — `src/trading/` modular package
2. **Tests** — `tests/` unit, property, integration, replay, backtest
3. **Config** — `config.yaml`, `.env.example`
4. **Docs** — `AGENTS.md`, `PLAN.md`, API docs, architecture diagram
5. **Runbook** — `RUNBOOK.md`: start, stop, backup, recover, monitor
6. **Report** — Final summary with metrics, limitations, production gaps

---

## Timeline Summary

| Week | Phases | Key Milestone |
|------|--------|---------------|
| 1 | 1-2 | Schemas, validation, dev env |
| 2 | 3-4 | Persistence, ingestion pipeline |
| 3 | 5-6 | News/calendar, fundamental engine |
| 4 | 7-8 | Technical engine, risk engine |
| 5 | 9-11 | Decision, AXLE, audit, API |
| 6 | 12-13 | Full test suite, E2E validation |