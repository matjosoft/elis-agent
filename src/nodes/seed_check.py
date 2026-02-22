import logging

from src.config import DB_PATH
from src.db import get_connection, get_consumption_count, get_seed_status
from src.state import AgentState

logger = logging.getLogger(__name__)


async def check_seed_status_node(state: AgentState) -> dict:
    """
    Check whether the historical database is fully seeded.
    Sets is_seeded=True when seeding is complete, False otherwise.
    """
    if state.get("error"):
        return {}

    conn = get_connection(DB_PATH)
    try:
        last_month, is_complete = get_seed_status(conn)
        count = get_consumption_count(conn)

        if is_complete:
            logger.info("Database fully seeded (%d records)", count)
            return {"is_seeded": True, "seed_progress": f"Complete: {count} records"}

        if last_month:
            logger.info(
                "Seeding incomplete, resuming from after %s (%d records so far)",
                last_month,
                count,
            )
            return {
                "is_seeded": False,
                "seed_progress": f"Resuming from after {last_month} ({count} records)",
            }

        logger.info("No seed data found, starting from scratch")
        return {"is_seeded": False, "seed_progress": "Starting fresh"}
    finally:
        conn.close()
