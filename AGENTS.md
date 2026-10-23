# AGENTS.md — Quantitative Trading MVP Agent Rules

## Purpose
This file defines the coding standards, safety rules, and agent behavior for the Quantitative Trading MVP project. All agents (human or AI) working on this repository must follow these rules.

---

## 1. Architecture Principles

### Modular Monolith
- Build a **single Python package** with clear internal boundaries
- No microservices, no message queues, no external orchestration
- Each component in its own module with explicit public API
- Shared state only through well-defined data structures

### Responsibility Separation
| Module | Responsibility |
|--------|---------------|
| `ingestion` | Market data, news, calendar fetch & normalization |
| `persistence` | WAL, SQLite storage, crash recovery |
| `canonical` | Schema validation, type conversion, invariants |
| `fundamental` | AI-driven news/calendar analysis |
| `technical` | Deterministic indicator calculation |
| `risk` | Hard risk gates, position sizing |
| `decision` | `OPEN`/`WAIT`/`SKIP` logic |
| `audit` | Immutable decision trail |
| `axle` | Lean 4 mathematical verification |
| `api` | Minimal REST endpoints |

---

## 2. Coding Standards

### Python Version
- **Python 3.11+** required
- Use `from __future__ import annotations`
- Type hints mandatory on all public functions

### Type System
```python
# Use TypedDict for external data shapes
class TickData(TypedDict):
    symbol: str
    bid: Decimal
    ask: Decimal
    mid: Decimal
    ...

# Use Protocol for interfaces
class MarketDataProvider(Protocol):
    def fetch_latest(self, symbols: list[str]) -> list[TickData]: ...

# Use dataclasses for internal DTOs
@dataclass(frozen=True, slots=True)
class CanonicalTick:
    event_id: uuid.UUID
    provider: str
    provider_symbol: str
    canonical_symbol: str
    provider_ts: datetime
    received_ts: datetime
    persisted_ts: datetime
    bid: Decimal
    ask: Decimal
    mid: Decimal
    spread: Decimal
    sequence: int | None
    raw_payload_hash: str
    schema_version: int
    quality: QualityStatus
```

### Decimal for Financial Math
- **Never use `float`** for prices, quantities, risk calculations
- Use `decimal.Decimal` with explicit context
- Instrument precision from provider metadata

```python
from decimal import Decimal, getcontext, ROUND_HALF_EVEN

getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN

INSTRUMENT_PRECISION = {
    "XAUUSD": Decimal("0.01"),   # 2 decimals
    "EURUSD": Decimal("0.00001"), # 5 decimals
    "GBPUSD": Decimal("0.00001"),
    "WTI":    Decimal("0.01"),
}
```

### Immutability
- All DTOs: `@dataclass(frozen=True, slots=True)`
- No in-place mutations of canonical data
- New snapshot = new object

### Error Handling
- **No silent failures** — every exception logged with context
- Custom exception hierarchy:
```python
class TradingError(Exception): pass
class DataQualityError(TradingError): pass
class GapDetectedError(DataQualityError): pass
class StaleQuoteError(DataQualityError): pass
class RiskLimitExceeded(TradingError): pass
class AXLEVerificationFailed(TradingError): pass
```
- Fail-closed: on persistence error, block new decisions

---

## 3. Safety Rules

### No Live Trading
- **Never** send orders to a broker
- No execution engine, no order management
- MVP = analysis + decision plan + simulation only

### Data Integrity
- Every tick persisted **before** processing
- Raw payload hash stored for audit
- Duplicate detection on `event_id` + `provider_symbol` + `provider_ts`
- Gap detection → block `OPEN` decisions

### Risk Gates (Hard, Non-Overridable)
| Gate | Limit | Action on Breach |
|------|-------|------------------|
| Per-trade risk | 2% max | `SKIP` |
| Daily loss limit | 3% | Block all new `OPEN` |
| Max trades/day | 3 | Block all new `OPEN` |
| Min RR | 1:2 | `SKIP` |
| Stale quote (>5 min) | — | `SKIP` |
| Data gap | — | `SKIP` |
| Market closed | — | `SKIP` |
| News blackout | ±30 min | `SKIP` |

### AI Usage
- AI **never** calculates indicators, SL/TP, position size
- AI **only** analyzes news/calendar → fundamental thesis
- AI output parsed, validated, stored — never executed directly
- Model responses must include: facts, hypotheses, counter-arguments, sources

---

## 4. Testing Requirements

### Write Tests First (TDD)
- Unit tests for every pure function
- Property-based tests for invariants
- Integration tests for persistence, providers, AI adapters
- All tests must pass before merging

### Test Categories
```bash
# Unit
pytest tests/unit -v

# Property (Hypothesis)
pytest tests/property -v

# Integration
pytest tests/integration -v

# Replay/Backtest
pytest tests/replay -v
```

### Mandatory Invariants (Property Tests)
- `high >= max(open, close)`
- `low <= min(open, close)`
- `high >= low`
- Deterministic output for same input
- Duplicate event doesn't change OHLC twice
- Risk/RR gates cannot be bypassed

---

## 5. MCP Integration Rules

### Preserve Existing MCP Servers
| Server | Purpose | Status |
|--------|---------|--------|
| `fin-data` | Market data, news, macro, SEC, congress | **Keep** |
| `searxng` | Web search for fundamental research | **Keep** |
| `axle` (via Python) | Lean 4 math verification | **Keep** |

### Adding New MCP
- Only if existing tools **cannot** provide required capability
- Document: tool name, schema, rate limits, auth
- Test before integrating

### AXLE Usage
- Verify: OHLC invariants, RSI/ATR formulas, entry/SL/TP math, risk calcs
- Call via Python `AxleClient` (not MCP) for lower latency
- Log every verification request + response
- If AXLE unavailable → log warning, continue with Python tests only

---

## 6. Configuration

### Environment Variables
```bash
# Required
BIQUOTE_WS_URL=wss://biquote.io/hubs/tick
AXLE_API_KEY=pk_...

# Optional (with defaults)
DB_PATH=./data/trading.db
JOURNAL_PATH=./data/journal.log
LOG_LEVEL=INFO
AI_MODEL_PROVIDER=openrouter
AI_MODEL_ID=inclusionAI/Ling-3.0-flash-Fin
```

### Config File (`config.yaml`)
```yaml
instruments:
  - XAUUSD
  - EURUSD
  - GBPUSD
  - WTI

risk:
  default_risk_pct: 0.01
  max_trade_risk_pct: 0.02
  daily_loss_limit_pct: 0.03
  max_trades_per_day: 3
  min_risk_reward: 2.0

technical:
  h1_timeframe: "1h"
  m15_timeframe: "15m"
  rsi_period: 14
  rsi_overbought: 70
  rsi_oversold: 30
  entry_buffer_points: 30

ai:
  temperature: 1.0
  top_p: 0.95
  top_k: 20
  max_tokens: 8192
```

---

## 7. Logging & Observability

### Structured Logging
```python
import structlog

logger = structlog.get_logger()

logger.info("tick_received",
    symbol="EURUSD",
    provider="biquote",
    event_id=str(event_id),
    latency_ms=latency_ms)
```

### Metrics (Prometheus format)
- `ticks_received_total{symbol,provider}`
- `ticks_persisted_total{symbol}`
- `gaps_detected_total{symbol}`
- `decisions_total{symbol,status}`
- `decision_latency_seconds{stage}`
- `ai_calls_total{model,status}`
- `axle_verifications_total{status}`

---

## 8. Git Workflow

### Branching
- `main` — always deployable
- `feature/*` — one feature per branch
- `fix/*` — bug fixes

### Commits
- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`
- One logical change per commit
- Tests in same commit as implementation

### Pre-commit Hooks
```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: ruff
        name: ruff
        entry: ruff check --fix
        language: system
      - id: mypy
        name: mypy
        entry: mypy --strict
        language: system
      - id: pytest
        name: pytest
        entry: pytest -x
        language: system
```

---

## 9. Documentation

### Every Module Has
- `__init__.py` with public API exports
- Module docstring explaining purpose
- Type hints on all public functions
- Example usage in docstring

### Decision Audit Format
```json
{
  "decision_id": "uuid",
  "snapshot_id": "uuid",
  "symbol": "XAUUSD",
  "status": "OPEN",
  "timestamp": "2026-10-23T10:30:00Z",
  "news_ids": ["news_123"],
  "calendar_ids": ["cal_456"],
  "technical": { "h1_structure": "...", "m15_rsi": 65, ... },
  "fundamental": { "thesis": "...", "sources": [...], ... },
  "entry": 348.52, "sl": 347.20, "tp1": 350.10, "tp2": 352.00,
  "risk_pct": 1.0, "rr": 2.5,
  "failed_gates": [],
  "strategy_version": "1.0.0",
  "data_quality": "clean",
  "axle_verification": { "ohlc_invariants": true, "rr_calc": true }
}
```

---

## 10. Prohibited Patterns

| Pattern | Reason |
|---------|--------|
| `float` for prices | Rounding errors |
| Global mutable state | Non-determinism |
| `try: ... except: pass` | Silent failures |
| AI calculating SL/TP | Unverifiable |
| Hardcoded API keys | Security |
| Polling instead of WS | Latency, rate limits |
| Look-ahead in backtest | Invalid results |
| Synthetic data as real | Misleading |

---

## 11. Agent Behavior

### When Uncertain
- **Stop and ask** — don't guess API formats, model IDs, or configs
- Check official docs first
- Document assumptions explicitly

### When Implementing
1. Write failing test first
2. Implement minimal code to pass
3. Run full test suite
4. Update docs

### When Reviewing
- Check: types, invariants, error handling, tests
- Verify: no float math, no silent failures, no AI in hot path
- Confirm: MCP usage matches documented tools

---

## 12. Emergency Procedures

### Data Corruption
1. Stop ingestion
2. Replay journal from last good checkpoint
3. Verify canonical store integrity
4. Resume

### AXLE Unavailable
1. Log warning
2. Continue with Python property tests
3. Queue verifications for retry
4. Alert on extended outage

### Provider Outage
1. Detect via stale quotes
2. Switch to backup provider (if configured)
3. Block `OPEN` if no reliable source
4. Alert