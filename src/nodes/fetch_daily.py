import logging
from datetime import date, datetime, timezone

from langchain_mcp_adapters.client import MultiServerMCPClient

from src.config import DB_PATH, get_mcp_client_config
from src.db import (
    get_baseline_metrics,
    get_connection,
    get_latest_consumption_timestamp,
    get_latest_production_timestamp,
    get_monthly_daily_rows,
    get_monthly_production_rows,
    get_today_hourly,
    insert_consumption_batch,
    insert_production_batch,
)
from src.parsers import parse_historic_json, parse_production_json
from src.state import AgentState

logger = logging.getLogger(__name__)

_MAX_DELTA_HOURS = 744  # cap at ~1 month to avoid huge requests


async def fetch_daily_node(state: AgentState) -> dict:
    """
    1. Fetch consumption data since the last stored timestamp (delta update).
    2. Fetch today's + tomorrow's price forecast.
    3. Compute baseline metrics from the full historical dataset.
    """
    if state.get("error"):
        return {}

    home_id = state.get("home_id", "")

    client = MultiServerMCPClient(get_mcp_client_config())
    tools = await client.get_tools()

    get_historic_json = next(
        (t for t in tools if t.name == "get-historic-json"), None
    )
    get_price_forecast = next(
        (t for t in tools if t.name == "get-price-forecast"), None
    )

    if get_historic_json is None or get_price_forecast is None:
        return {"error": "Required MCP tools (get-historic-json / get-price-forecast) not found"}

    conn = get_connection(DB_PATH)
    try:
        # ----------------------------------------------------------------
        # 1. Delta consumption
        # ----------------------------------------------------------------
        latest_ts = get_latest_consumption_timestamp(conn)
        hours_to_fetch = _MAX_DELTA_HOURS if latest_ts is None else min(_hours_since(latest_ts) + 1, _MAX_DELTA_HOURS)

        logger.info("Fetching %d hours of delta consumption", hours_to_fetch)
        consumption_result = await get_historic_json.ainvoke(
            {
                "home_id": home_id,
                "resolution": "HOURLY",
                "count": hours_to_fetch,
            }
        )
        consumption_text = _to_text(consumption_result)
        recent_records = parse_historic_json(consumption_text)
        if recent_records:
            inserted = insert_consumption_batch(conn, recent_records)
            logger.info("Delta: inserted %d new records", inserted)

        # ----------------------------------------------------------------
        # 2. Price forecast
        # ----------------------------------------------------------------
        logger.info("Fetching price forecast")
        price_result = await get_price_forecast.ainvoke({"home_id": home_id})
        price_text = _to_text(price_result)

        # ----------------------------------------------------------------
        # 3. Baseline metrics
        # ----------------------------------------------------------------
        baseline = get_baseline_metrics(conn)
        logger.info(
            "Baseline: %d total hours, avg %.3f kWh",
            baseline.get("total_hours", 0),
            baseline.get("overall_avg_kwh") or 0,
        )

        # ----------------------------------------------------------------
        # 5. Delta production
        # ----------------------------------------------------------------
        latest_prod_ts = get_latest_production_timestamp(conn)
        # If no production data at all, fetch the full cap to backfill the month
        prod_hours = _MAX_DELTA_HOURS if latest_prod_ts is None else min(_hours_since(latest_prod_ts) + 1, _MAX_DELTA_HOURS)

        logger.info("Fetching %d hours of delta production", prod_hours)
        try:
            production_result = await get_historic_json.ainvoke(
                {
                    "home_id": home_id,
                    "resolution": "HOURLY",
                    "count": prod_hours,
                    "production": True,
                }
            )
            production_text = _to_text(production_result)
            production_records = parse_production_json(production_text)
            if production_records:
                inserted_prod = insert_production_batch(conn, production_records)
                logger.info("Delta production: inserted %d new records", inserted_prod)
        except Exception as prod_exc:
            # Production may not be available for all homes — log but don't fail
            logger.warning("Production fetch skipped: %s", prod_exc)

        run_date = state.get("run_date", date.today().isoformat())
        today_hourly = get_today_hourly(conn, run_date)
        year_month = run_date[:7]  # YYYY-MM
        monthly_daily = get_monthly_daily_rows(conn, year_month)
        monthly_daily_production = get_monthly_production_rows(conn, year_month)

        return {
            "current_prices": price_text,
            "recent_consumption": consumption_text,
            "baseline_metrics": baseline,
            "today_hourly": today_hourly,
            "monthly_daily": monthly_daily,
            "monthly_daily_production": monthly_daily_production,
        }

    except Exception as exc:
        logger.error("fetch_daily failed: %s", exc, exc_info=True)
        return {"error": f"fetch_daily failed: {exc}"}
    finally:
        conn.close()


def _hours_since(iso_timestamp: str) -> int:
    """Hours between an ISO timestamp and now, minimum 1."""
    try:
        then = datetime.fromisoformat(iso_timestamp)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(int((now - then).total_seconds() / 3600), 1)
    except ValueError:
        return _MAX_DELTA_HOURS


def _to_text(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif hasattr(item, "text"):
                parts.append(item.text)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
        return "\n".join(parts)
    return str(result)
