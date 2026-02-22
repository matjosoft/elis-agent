import logging

from src.config import DB_PATH
from src.db import get_connection, insert_analysis
from src.state import AgentState

logger = logging.getLogger(__name__)


async def storage_node(state: AgentState) -> dict:
    """Persist the analysis JSON and markdown report to SQLite."""
    if state.get("error"):
        return {}

    run_date = state.get("run_date", "")
    analysis_json = state.get("analysis_json", "{}")
    summary_markdown = state.get("summary_markdown", "")

    conn = get_connection(DB_PATH)
    try:
        insert_analysis(conn, run_date, analysis_json, summary_markdown)
        logger.info("Stored analysis for %s", run_date)
        return {}
    except Exception as exc:
        logger.error("Storage failed: %s", exc, exc_info=True)
        return {"error": f"Storage failed: {exc}"}
    finally:
        conn.close()
