# Trading MVP - Runbook

## Overview

Runbook for deploying and operating the Quantitative Trading MVP.

**No live trading** - analysis, decision plans, simulation, and audit only.

## Architecture

```
Market/News Feeds → Durable Ingestion → Canonical Data → Fundamental + Technical Analysis → Risk Gates → Decision → Audit
```

## Components

| Module | Responsibility |
|--------|---------------|
| `canonical` | Schema, types, invariants, validation |
| `config` | Configuration loading (YAML + env) |
| `persistence` | WAL journal, SQLite WAL, crash recovery |
| `ingestion` | biquote WS/REST, news, calendar, OHLC builder |
| `fundamental` | AI (Ling-3.0-flash-Fin) news/calendar analysis |
| `technical` | H1 structure, M15 RSI, patterns, SL/TP |
| `risk` | Hard gates (spread, RR, daily limit, blackout) |
| `decision` | OPEN/WAIT/SKIP deterministic engine |
| `axle` | Lean 4 math verification (OHLC, RSI, RR) |
| `audit` | Immutable decision trail |
| `api` | Minimal REST endpoints |
| `main` | Application entry point |

## Installation

### Prerequisites
- Python 3.11+
- Internet connection (for biquote API, OpenRouter, AXLE API)

### Setup

```bash
# Clone or navigate to project
cd /home/main/Desktop/project.invest-az

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Install AXLE (separate venv or same)
pip install axiom-axle

# Copy environment template
cp .env.example .env

# Edit .env with your API keys
nano .env
```

### Required Environment Variables

```bash
AXLE_API_KEY=your_axle_api_key
OPENROUTER_API_KEY=your_openrouter_api_key
```

### Optional Environment Variables

```bash
BIQUOTE_WS_URL=wss://biquote.io/hubs/tick
DB_PATH=./data/trading.db
JOURNAL_PATH=./data/journal.log
LOG_LEVEL=INFO
AI_MODEL_PROVIDER=openrouter
AI_MODEL_ID=inclusionAI/Ling-3.0-flash-Fin
```

## Running the Application

### Full Application (Ingestion + API)

```bash
source .venv/bin/activate
export AXLE_API_KEY=your_key
export OPENROUTER_API_KEY=your_key

python -m trading.main --config config.yaml
```

### API Only Mode

```bash
source .venv/bin/activate
export AXLE_API_KEY=your_key
export OPENROUTER_API_KEY=your_key

python -m trading.main --api-only --host 127.0.0.1 --port 8080
```

### Ingestion Only (for testing)

```bash
source .venv/bin/activate
python -c "
import asyncio
from trading.persistence import EventJournal, SQLiteStore
from trading.ingestion import IngestionPipeline

async def main():
    journal = EventJournal(path='./data/journal.log')
    store = SQLiteStore(db_path='./data/trading.db')
    pipeline = IngestionPipeline(journal, store, symbols=['EURUSD', 'XAUUSD', 'GBPUSD', 'WTI'])
    await pipeline.start()
    # Let it run for some time
    await asyncio.sleep(60)
    await pipeline.stop()

asyncio.run(main())
"
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /health` | GET | Health check with component status |
| `GET /metrics` | GET | Prometheus metrics |
| `GET /market/{symbol}` | GET | Latest market data |
| `GET /candles/{symbol}` | GET | OHLC candles (timeframe, limit params) |
| `GET /news` | GET | Recent news (hours, limit params) |
| `GET /calendar` | GET | Upcoming calendar events |
| `GET /analysis/{symbol}` | GET | Latest analysis summary |
| `GET /decisions` | GET | Recent decisions (symbol, status filters) |
| `GET /decisions/{decision_id}` | GET | Specific decision audit trail |

### Example API Calls

```bash
# Health check
curl http://127.0.0.1:8080/health

# Get EURUSD market data
curl http://127.0.0.1:8080/market/EURUSD

# Get H1 candles
curl "http://127.0.0.1:8080/candles/EURUSD?timeframe=1h&limit=100"

# Get recent decisions
curl "http://127.0.0.1:8080/decisions?symbol=XAUUSD&status=SKIP"

# Get Prometheus metrics
curl http://127.0.0.1:8080/metrics
```

## Configuration

### config.yaml

```yaml
instruments:
  - "XAUUSD"
  - "EURUSD"
  - "GBPUSD"
  - "WTI"

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
  entry_buffer_points: 30

ai:
  provider: "openrouter"
  model: "inclusionAI/Ling-3.0-flash-Fin"
  temperature: 1.0
  timeout_seconds: 30
```

## Persistence & Recovery

### Database Location
- SQLite: `./data/trading.db` (configurable in config.yaml)
- Journal: `./data/journal.log` (configurable in config.yaml)

### Backup

```bash
# Backup database
python -c "
from trading.persistence.store import SQLiteStore
store = SQLiteStore(db_path='./data/trading.db')
store.backup('./data/backup_$(date +%Y%m%d).db')
"

# Restore database
python -c "
from trading.persistence.store import SQLiteStore
store = SQLiteStore(db_path='./data/trading.db')
store.restore('./data/backup_20261023.db')
"
```

### Journal Replay

```bash
# Replay journal from checkpoint
python -c "
from trading.persistence.journal import EventJournal
journal = EventJournal(path='./data/journal.log')
for event in journal.replay(from_sequence=0):
    print(event)
"
```

### Integrity Check

```bash
python -c "
from trading.persistence.store import SQLiteStore
store = SQLiteStore(db_path='./data/trading.db')
ok, errors = store.verify_integrity()
print(f'Integrity: {ok}, Errors: {errors}')
"
```

## MCP Servers

### Preserved (do not modify)

| Server | Tools | Purpose |
|--------|-------|---------|
| `fin-data` | 83 tools / 15 providers | Market data, news, macro, SEC, congress |
| `searxng` | 4 tools | Web search for fundamental research |
| `axle` | Python client | Lean 4 math verification |

### OpenCode Configuration

The OpenCode MCP configuration is at `opencode.json`. Do not modify without backup.

```bash
# Backup before changes
cp opencode.json opencode.json.backup
```

## Monitoring

### Health Check
```bash
curl http://127.0.0.1:8080/health
```

### Prometheus Metrics
```bash
curl http://127.0.0.1:8080/metrics | head -50
```

### Logs
Application logs go to stdout and `./data/trading.log` (if configured).

## Troubleshooting

### Issue: No market data received
**Cause:** biquote API unreachable or WebSocket connection failed
**Fix:** Check internet connectivity, verify biquote.io is accessible

### Issue: AXLE verification failing
**Cause:** AXLE API key invalid or service unavailable
**Fix:** Check `AXLE_API_KEY` in .env, verify AXLE service status

### Issue: Decision always SKIP
**Cause:** Risk gates failing (spread, data quality, market hours, etc.)
**Fix:** Check `/health` endpoint, verify data quality, check market hours

### Issue: Database locked
**Cause:** Concurrent write access
**Fix:** SQLite WAL mode handles this automatically; ensure only one writer

### Issue: Journal corruption
**Cause:** Crash during write
**Fix:** Run integrity check, restore from backup if needed

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test category
pytest tests/unit -v
pytest tests/property -v
pytest tests/integration -v
pytest tests/replay -v

# Run with coverage
pytest tests/ --cov=src/trading --cov-report=html
```

## Data Integrity Guarantees

- **No silent failures** - every exception logged with context
- **Append-only journal** - SHA256 hash per line for integrity
- **SQLite WAL mode** - crash-safe concurrent reads/writes
- **Duplicate detection** - event_id + provider_symbol + provider_ts
- **Gap detection** - sequence and time gaps detected and logged
- **Immutable decisions** - never modified, corrections are new events
- **OHLC invariants** - enforced at construction time

## Security

- No API keys in code or logs
- No authentication on API (internal tool only)
- No live trading capability
- All configuration via environment variables

## Support

For issues, check:
1. `/health` endpoint for component status
2. Application logs for errors
3. Journal integrity for data corruption
4. Config file for incorrect parameters