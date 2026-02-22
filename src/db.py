import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consumption_history (
    timestamp TEXT NOT NULL,
    consumption_kwh REAL,
    cost REAL,
    unit_price REAL,
    unit_price_vat REAL,
    PRIMARY KEY (timestamp)
);
CREATE INDEX IF NOT EXISTS idx_consumption_timestamp
    ON consumption_history(timestamp);

CREATE TABLE IF NOT EXISTS seed_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_fetched_month TEXT NOT NULL,
    is_complete INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    summary_markdown TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_analysis_run_date
    ON analysis_results(run_date);

CREATE TABLE IF NOT EXISTS daily_prices (
    timestamp TEXT NOT NULL,
    total_price REAL,
    energy_price REAL,
    tax REAL,
    level TEXT,
    PRIMARY KEY (timestamp)
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database and ensure the schema exists."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Seed status
# ---------------------------------------------------------------------------


def get_seed_status(conn: sqlite3.Connection) -> tuple[Optional[str], bool]:
    """Return (last_fetched_month, is_complete). (None, False) if no seed started."""
    row = conn.execute(
        "SELECT last_fetched_month, is_complete FROM seed_status WHERE id = 1"
    ).fetchone()
    if row is None:
        return None, False
    return row["last_fetched_month"], bool(row["is_complete"])


def update_seed_status(
    conn: sqlite3.Connection, month: str, is_complete: bool
) -> None:
    """Upsert the singleton seed-status row."""
    conn.execute(
        "INSERT INTO seed_status (id, last_fetched_month, is_complete) VALUES (1, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET"
        "   last_fetched_month = excluded.last_fetched_month,"
        "   is_complete = excluded.is_complete",
        (month, int(is_complete)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Consumption data
# ---------------------------------------------------------------------------


def insert_consumption_batch(
    conn: sqlite3.Connection, records: list[dict]
) -> int:
    """Insert records, skipping duplicates. Returns number of rows inserted."""
    inserted = 0
    for rec in records:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO consumption_history"
            " (timestamp, consumption_kwh, cost, unit_price, unit_price_vat)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                rec["timestamp"],
                rec.get("consumption_kwh"),
                rec.get("cost"),
                rec.get("unit_price"),
                rec.get("unit_price_vat"),
            ),
        )
        inserted += cursor.rowcount
    conn.commit()
    return inserted


def get_latest_consumption_timestamp(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT MAX(timestamp) AS latest FROM consumption_history"
    ).fetchone()
    return row["latest"] if row else None


def get_today_hourly(conn: sqlite3.Connection, run_date: str) -> list[dict]:
    """Return hourly rows for *run_date* (YYYY-MM-DD), ordered by timestamp."""
    rows = conn.execute(
        "SELECT timestamp, consumption_kwh, cost, unit_price"
        " FROM consumption_history"
        " WHERE timestamp >= ? AND timestamp < date(?, '+1 day')"
        " ORDER BY timestamp",
        (run_date, run_date),
    ).fetchall()
    return [dict(r) for r in rows]


def get_consumption_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM consumption_history"
    ).fetchone()
    return row["cnt"]


# ---------------------------------------------------------------------------
# Baseline metrics (pre-aggregated for LLM context)
# ---------------------------------------------------------------------------


def get_baseline_metrics(conn: sqlite3.Connection) -> dict:
    """
    Compute summary statistics from the full consumption history.
    Returns a dict that fits comfortably in the LLM context window.
    """
    # Overall averages
    overall = conn.execute(
        "SELECT"
        "  AVG(consumption_kwh) AS avg_kwh,"
        "  AVG(cost)            AS avg_cost,"
        "  AVG(unit_price)      AS avg_unit_price,"
        "  MIN(unit_price)      AS min_unit_price,"
        "  MAX(unit_price)      AS max_unit_price,"
        "  COUNT(*)             AS total_hours"
        " FROM consumption_history"
        " WHERE consumption_kwh IS NOT NULL"
    ).fetchone()

    # Monthly averages (e.g. last 24 months to keep size bounded)
    monthly_rows = conn.execute(
        "SELECT"
        "  strftime('%Y-%m', timestamp) AS month,"
        "  AVG(consumption_kwh)         AS avg_kwh,"
        "  AVG(cost)                    AS avg_cost"
        " FROM consumption_history"
        " WHERE consumption_kwh IS NOT NULL"
        " GROUP BY month"
        " ORDER BY month DESC"
        " LIMIT 24"
    ).fetchall()

    # Hourly consumption profile (average kWh per hour-of-day across all history)
    hourly_rows = conn.execute(
        "SELECT"
        "  CAST(strftime('%H', timestamp) AS INTEGER) AS hour,"
        "  AVG(consumption_kwh) AS avg_kwh"
        " FROM consumption_history"
        " WHERE consumption_kwh IS NOT NULL"
        " GROUP BY hour"
        " ORDER BY hour"
    ).fetchall()

    # Price percentiles (approximate using SQLite window / NTILE isn't available in 3.12)
    price_rows = conn.execute(
        "SELECT unit_price FROM consumption_history"
        " WHERE unit_price IS NOT NULL"
        " ORDER BY unit_price"
    ).fetchall()
    prices = [r["unit_price"] for r in price_rows]
    percentiles = _percentiles(prices, [10, 25, 50, 75, 90]) if prices else {}

    return {
        "total_hours": overall["total_hours"] if overall else 0,
        "overall_avg_kwh": _round(overall["avg_kwh"]),
        "overall_avg_cost": _round(overall["avg_cost"]),
        "overall_avg_unit_price": _round(overall["avg_unit_price"]),
        "unit_price_min": _round(overall["min_unit_price"]),
        "unit_price_max": _round(overall["max_unit_price"]),
        "unit_price_percentiles": percentiles,
        "monthly_averages": [
            {
                "month": r["month"],
                "avg_kwh": _round(r["avg_kwh"]),
                "avg_cost": _round(r["avg_cost"]),
            }
            for r in monthly_rows
        ],
        "hourly_profile": [
            {"hour": r["hour"], "avg_kwh": _round(r["avg_kwh"])}
            for r in hourly_rows
        ],
    }


def _percentiles(sorted_values: list[float], pcts: list[int]) -> dict:
    n = len(sorted_values)
    result = {}
    for p in pcts:
        idx = int(p / 100 * n)
        idx = max(0, min(idx, n - 1))
        result[f"p{p}"] = round(sorted_values[idx], 4)
    return result


def _round(value) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 4)


# ---------------------------------------------------------------------------
# Analysis results
# ---------------------------------------------------------------------------


def insert_analysis(
    conn: sqlite3.Connection,
    run_date: str,
    analysis_json: str,
    summary_markdown: str,
) -> None:
    conn.execute(
        "INSERT INTO analysis_results (run_date, analysis_json, summary_markdown)"
        " VALUES (?, ?, ?)",
        (run_date, analysis_json, summary_markdown),
    )
    conn.commit()


def get_latest_analysis(conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM analysis_results ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(row)
