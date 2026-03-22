from typing import Optional

from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """LangGraph state shared across all nodes."""

    # Discovered during initialization
    home_id: str

    # Seeding
    is_seeded: bool
    seed_progress: str

    # Daily data fetched before analysis
    current_prices: str        # raw text from get-price-forecast
    recent_consumption: str    # raw JSON string from get-historic-json
    baseline_metrics: dict     # pre-aggregated SQL stats
    today_hourly: list         # hourly rows for today (from DB)
    monthly_daily: list        # daily aggregates for the current month (from DB)
    monthly_daily_production: list  # daily production aggregates for the current month (from DB)

    # Analysis output
    analysis_json: str
    summary_markdown: str

    # Control flow
    error: Optional[str]
    run_date: str
