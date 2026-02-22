import logging
from pathlib import Path
from typing import Literal

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from src.config import DB_PATH
from src.nodes.analysis import analysis_node
from src.nodes.fetch_daily import fetch_daily_node
from src.nodes.initialization import initialization_node
from src.nodes.reporting import reporting_node
from src.nodes.seed_check import check_seed_status_node
from src.nodes.seeding import seeding_node
from src.nodes.storage import storage_node
from src.state import AgentState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conditional routing functions
# ---------------------------------------------------------------------------


def _route_after_seed_check(
    state: AgentState,
) -> Literal["seeding", "fetch_daily", "__end__"]:
    if state.get("error"):
        return "__end__"
    if state.get("is_seeded"):
        return "fetch_daily"
    return "seeding"


def _route_after_fetch(
    state: AgentState,
) -> Literal["analysis", "__end__"]:
    if state.get("error"):
        return "__end__"
    return "analysis"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("initialization", initialization_node)
    builder.add_node("check_seed_status", check_seed_status_node)
    builder.add_node("seeding", seeding_node)
    builder.add_node("fetch_daily", fetch_daily_node)
    builder.add_node("analysis", analysis_node)
    builder.add_node("reporting", reporting_node)
    builder.add_node("storage", storage_node)

    builder.add_edge(START, "initialization")
    builder.add_edge("initialization", "check_seed_status")

    builder.add_conditional_edges(
        "check_seed_status",
        _route_after_seed_check,
        {
            "seeding": "seeding",
            "fetch_daily": "fetch_daily",
            "__end__": END,
        },
    )

    builder.add_edge("seeding", "fetch_daily")

    builder.add_conditional_edges(
        "fetch_daily",
        _route_after_fetch,
        {
            "analysis": "analysis",
            "__end__": END,
        },
    )

    # reporting runs first so summary_markdown is in state when storage saves it
    builder.add_edge("analysis", "reporting")
    builder.add_edge("reporting", "storage")
    builder.add_edge("storage", END)

    return builder


def compile_graph(checkpointer: AsyncSqliteSaver):
    """Compile the graph with an already-opened async SQLite checkpointer."""
    builder = build_graph()
    return builder.compile(checkpointer=checkpointer)


def checkpoint_db_path() -> str:
    path = DB_PATH.replace(".db", "-checkpoints.db")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path
