# Elis Agent — Implementation Plan

## Context

The project needs an autonomous energy analyst agent for Raspberry Pi that ingests 4 years of historical hourly consumption data from Tibber, then runs daily LLM-driven analysis comparing current prices against the historical baseline. The project is greenfield — only `docs/initial-spec.md` exists. The Tibber MCP server is already installed at `/home/mattias/develop1/mcp/tibber-mcp/` and exposes a `get-historic-json` tool that returns structured JSON (ideal for seeding).

## Project Structure

```
elis-agent/
├── docs/initial-spec.md          (exists)
├── src/
│   ├── __init__.py
│   ├── main.py                   # entry point (cron or manual)
│   ├── config.py                 # env loading, LLM/MCP factories
│   ├── state.py                  # AgentState TypedDict
│   ├── db.py                     # SQLite schema, helpers, baseline metrics
│   ├── graph.py                  # LangGraph graph definition
│   ├── parsers.py                # shared MCP response parsers
│   └── nodes/
│       ├── __init__.py
│       ├── initialization.py     # MCP connect, discover home_id
│       ├── seed_check.py         # check if historical data exists
│       ├── seeding.py            # chunked month-by-month fetch
│       ├── fetch_daily.py        # delta consumption + price forecast
│       ├── analysis.py           # LLM analysis node
│       ├── storage.py            # persist analysis to SQLite
│       └── reporting.py          # generate markdown report
├── pyproject.toml
├── .env.example
└── .gitignore
```

## Technical Stack

| Component | Technology |
|---|---|
| Orchestration | LangGraph (Python) |
| MCP Integration | `langchain-mcp-adapters` MultiServerMCPClient (stdio) |
| Database | SQLite (`sqlite3` for app, `langgraph-checkpoint-sqlite` for graph state) |
| LLM | OpenRouter via `langchain-openai` ChatOpenAI with custom base_url |
| Config | `python-dotenv` |

## Implementation Steps

### Step 1: Project Scaffolding

Create `pyproject.toml`, `.env.example`, `.gitignore`, and empty `__init__.py` files.

**Dependencies:** `langgraph`, `langgraph-checkpoint-sqlite`, `langchain-mcp-adapters`, `langchain-openai`, `langchain`, `python-dotenv`, `python-dateutil`

**`.env.example`** keys: `TIBBER_TOKEN`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `TIBBER_MCP_PATH`, `DB_PATH`, `REPORTS_PATH`, `LOG_LEVEL`

### Step 2: `src/config.py` — Configuration

- Load `.env` with `python-dotenv`
- Export constants: API keys, paths, seeding params (`SEED_START_DATE = "2022-04-01"`, `SEED_CHUNK_HOURS = 744`)
- Factory: `get_llm()` → `ChatOpenAI(base_url="https://openrouter.ai/api/v1", ...)`
- Factory: `get_mcp_client_config()` → dict for `MultiServerMCPClient` with stdio transport pointing to the Tibber MCP server

### Step 3: `src/db.py` — Database Module

**SQLite tables:**
- `consumption_history` — `timestamp` (PK, indexed), `consumption_kwh`, `cost`, `unit_price`, `unit_price_vat`
- `seed_status` — singleton row tracking `last_fetched_month` and `is_complete`
- `analysis_results` — `run_date`, `analysis_json`, `summary_markdown`, `created_at`
- `daily_prices` — `timestamp` (PK), `total_price`, `energy_price`, `tax`, `level`

**Key helpers:**
- `get_connection(db_path)` — opens DB with `WAL` journal mode (better for Pi SD cards), creates schema
- `get_seed_status()` / `update_seed_status()` — checkpoint for seeding resumption
- `insert_consumption_batch(records)` — bulk insert with `INSERT OR IGNORE` (idempotent)
- `get_baseline_metrics()` — SQL aggregations returning overall averages, monthly/hourly profiles, price percentiles

### Step 4: `src/state.py` — LangGraph State

`AgentState(TypedDict)` with fields: `home_id`, `mcp_tools`, `is_seeded`, `seed_progress`, `current_prices`, `recent_consumption`, `baseline_metrics`, `analysis_json`, `summary_markdown`, `error`, `run_date`

### Step 5: `src/parsers.py` — Shared Parsers

- `parse_historic_json(result)` — converts `get-historic-json` output (`{"from", "consumption", "cost", "unitPrice", ...}`) to DB-ready dicts
- `parse_home_id(text)` — extracts home ID from `list-homes` text response

### Step 6: Node Implementations

**`initialization.py`** — Connect via `MultiServerMCPClient`, get tools, call `list-homes` to discover `home_id`. Errors stored in `state["error"]`.

**`seed_check.py`** — Query `seed_status` table. Set `is_seeded=True/False`. This is the conditional routing point.

**`seeding.py`** — Most complex node:
- Iterate month-by-month from `SEED_START_DATE` (or resume point) to present
- Call `get-historic-json` with `start_date=YYYY-MM-01`, `count=744` for each month
- Parse JSON, insert batch into `consumption_history`
- Update `seed_status` after each successful month (checkpoint for resumption)
- `asyncio.sleep(1)` between chunks for rate limiting
- On error: return immediately with error — next run retries from last successful month

**`fetch_daily.py`** — Fetch delta consumption since last stored timestamp via `get-historic-json`, fetch price forecast via `get-price-forecast`, compute `baseline_metrics` from historical data.

**`analysis.py`** — Send baseline metrics + current prices + recent consumption to LLM with "Senior Energy Strategist" system prompt. Request structured JSON output covering: price percentile, anomalies, peak/off-peak, savings estimates, recommendations.

**`reporting.py`** — Format analysis JSON into a markdown report, write to `reports/report-YYYY-MM-DD.md`.

**`storage.py`** — Persist analysis JSON and markdown summary to `analysis_results` table.

### Step 7: `src/graph.py` — Graph Construction

```
START → initialization → check_seed_status
    ├── (not seeded) → seeding → fetch_daily
    ├── (seeded) ─────────────→ fetch_daily
    └── (error) → END
                                fetch_daily
                                ├── (ok) → analysis → reporting → storage → END
                                └── (error) → END
```

- Use `SqliteSaver.from_conn_string()` for graph checkpointing (separate DB file from app data)
- Compile with `graph.compile(checkpointer=checkpointer)`

### Step 8: `src/main.py` — Entry Point

- `setup_logging()` — dual output to `agent.log` + stdout
- `run_agent()` — compile graph, `ainvoke({})` with `thread_id="elis-daily"`
- `main()` — sync wrapper via `asyncio.run()`
- Non-zero exit codes for cron monitoring (1 = handled error, 2 = crash)

## Key Design Decisions

| Decision | Rationale |
|---|---|
| `get-historic-json` over `get-historic` | Returns structured JSON, avoids fragile text parsing |
| Month-by-month seeding with checkpointing | Handles Pi power loss; `seed_status` tracks last good month |
| `INSERT OR IGNORE` for consumption | Makes partial retries idempotent — no duplicate rows |
| `PRAGMA journal_mode=WAL` | Better write performance on SD cards, survives power failures |
| Pre-computed baseline metrics via SQL | Keeps LLM context small (summary stats, not 35K rows) |
| Separate checkpoint DB from app DB | Avoids schema conflicts with LangGraph internals |
| Reporting before storage in graph | Ensures `summary_markdown` is in state when storage node runs |

## Tibber MCP Server Reference

**Location:** `/home/mattias/develop1/mcp/tibber-mcp/`

**Key tools used by the agent:**

| Tool | Parameters | Usage |
|---|---|---|
| `list-homes` | none | Discover `home_id` during initialization |
| `get-historic-json` | `home_id`, `resolution`, `count`, `start_date` | Seeding + daily delta fetch (returns raw JSON) |
| `get-price-forecast` | `home_id` | Today + tomorrow hourly prices |
| `get-consumption` | `home_id`, `hours` | Alternative for recent consumption |

**Response format from `get-historic-json`:**
```json
[
  {"from": "2022-04-01T00:00:00+02:00", "to": "...", "consumption": 1.23, "cost": 0.45, "unitPrice": 0.36, "unitPriceVAT": 0.09},
  ...
]
```

## Verification

1. **Seeding test:** Run agent, verify it starts from 2022-04-01 and inserts rows month by month. Interrupt mid-seed (Ctrl+C), restart — confirm it resumes from the correct month.
2. **Daily run test:** After seeding completes, run again — confirm it skips seeding, fetches delta data, produces analysis.
3. **Report check:** Inspect `reports/report-YYYY-MM-DD.md` for correct structure and sensible content.
4. **DB integrity:** Query `consumption_history` row count (~35K expected), verify no duplicate timestamps.
5. **Cron setup:** `0 7 * * * cd /home/mattias/elis-agent && uv run elis-agent >> cron.log 2>&1`
