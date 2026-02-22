import logging
from datetime import datetime, timezone

from langchain_mcp_adapters.client import MultiServerMCPClient

from src.config import DB_PATH, get_mcp_client_config
from src.db import (
    get_baseline_metrics,
    get_connection,
    get_latest_consumption_timestamp,
    insert_consumption_batch,
)
from src.parsers import parse_historic_json
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
        hours_to_fetch = min(_hours_since(latest_ts) + 1, _MAX_DELTA_HOURS)

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

        return {
            "current_prices": price_text,
            "recent_consumption": consumption_text,
            "baseline_metrics": baseline,
        }

    except Exception as exc:
        logger.error("fetch_daily failed: %s", exc, exc_info=True)
        return {"error": f"fetch_daily failed: {exc}"}
    finally:
        conn.close()


def _hours_since(iso_timestamp: str | None) -> int:
    """Hours between an ISO timestamp and now, minimum 48."""
    if not iso_timestamp:
        return 48
    try:
        then = datetime.fromisoformat(iso_timestamp)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta_hours = int((now - then).total_seconds() / 3600)
        return max(delta_hours, 1)
    except ValueError:
        return 48


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
