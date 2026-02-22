import asyncio
import logging
from datetime import date, datetime

from dateutil.relativedelta import relativedelta
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.config import (
    DB_PATH,
    SEED_CHUNK_HOURS,
    SEED_RATE_LIMIT_SECONDS,
    SEED_START_DATE,
    get_mcp_client_config,
)
from src.db import (
    get_connection,
    get_consumption_count,
    get_seed_status,
    insert_consumption_batch,
    update_seed_status,
)
from src.parsers import parse_historic_json
from src.state import AgentState

logger = logging.getLogger(__name__)


async def seeding_node(state: AgentState) -> dict:
    """
    Seed historical consumption data month-by-month from SEED_START_DATE to today.
    Supports resumption: skips months already marked in seed_status.
    """
    if state.get("error"):
        return {}

    home_id = state.get("home_id", "")

    client = MultiServerMCPClient(get_mcp_client_config())
    tools = await client.get_tools()

    get_historic_json = next(
        (t for t in tools if t.name == "get-historic-json"), None
    )
    if get_historic_json is None:
        return {"error": "get-historic-json tool not found"}

    conn = get_connection(DB_PATH)
    try:
        last_month, _ = get_seed_status(conn)

        if last_month:
            # Resume from the month after the last successfully fetched one
            start = (
                datetime.strptime(last_month, "%Y-%m").date()
                + relativedelta(months=1)
            )
        else:
            start = datetime.strptime(SEED_START_DATE, "%Y-%m-%d").date().replace(day=1)

        # Seed up to and including the current month
        end = date.today().replace(day=1)
        current = start

        while current <= end:
            month_str = current.strftime("%Y-%m")
            start_date_str = current.strftime("%Y-%m-%d")

            logger.info("Seeding month %s ...", month_str)

            try:
                result = await get_historic_json.ainvoke(
                    {
                        "home_id": home_id,
                        "resolution": "HOURLY",
                        "start_date": start_date_str,
                        "count": SEED_CHUNK_HOURS,
                    }
                )
                text = _to_text(result)
                records = parse_historic_json(text)

                if records:
                    inserted = insert_consumption_batch(conn, records)
                    logger.info(
                        "  %s: inserted %d / %d records", month_str, inserted, len(records)
                    )
                else:
                    logger.warning("  %s: no data returned", month_str)

            except Exception as exc:
                logger.error("  Error fetching %s: %s", month_str, exc, exc_info=True)
                # seed_status still points to last *good* month — next run retries here
                return {
                    "is_seeded": False,
                    "seed_progress": f"Error on {month_str}: {exc}",
                    "error": f"Seeding error on {month_str}: {exc}",
                }

            # Mark this month as successfully fetched (not yet complete overall)
            is_done = current >= end
            update_seed_status(conn, month_str, is_complete=is_done)

            current += relativedelta(months=1)
            if not is_done:
                await asyncio.sleep(SEED_RATE_LIMIT_SECONDS)

        count = get_consumption_count(conn)
        logger.info("Seeding complete: %d total records", count)
        return {
            "is_seeded": True,
            "seed_progress": f"Complete: {count} records",
        }
    finally:
        conn.close()


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
