import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import get_llm
from src.parsers import extract_json_from_llm
from src.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a Senior Energy Strategist analyzing electricity consumption \
and pricing data for a Swedish household. You have 4 years of historical consumption data \
as baseline context and access to today's spot prices.

Your analysis MUST cover:
1. **Price Percentile** — where today's average price sits in the 4-year distribution
2. **Consumption Anomalies** — compare recent usage against the historical hourly/monthly profile
3. **Peak/Off-Peak** — identify the cheapest and most expensive hours today
4. **Savings Estimate** — potential SEK savings from shifting flexible loads to off-peak hours
5. **Actionable Recommendations** — specific, time-bound actions (e.g. "Charge EV 02:00–05:00")

Respond with ONLY a JSON object (no prose outside the JSON):
{
  "price_percentile": <float 0-100>,
  "price_level": "VERY_LOW|LOW|NORMAL|HIGH|VERY_HIGH",
  "today_avg_price": <float>,
  "today_min_price": {"hour": <int>, "price": <float>},
  "today_max_price": {"hour": <int>, "price": <float>},
  "consumption_anomaly": <string or null>,
  "estimated_daily_savings_sek": <float>,
  "recommendations": [<string>, ...],
  "summary": "<2-3 sentences>"
}"""


async def analysis_node(state: AgentState) -> dict:
    """
    Send baseline metrics + current prices + recent consumption to the LLM
    and extract a structured JSON analysis.
    """
    if state.get("error"):
        return {}

    baseline = state.get("baseline_metrics", {})
    prices = state.get("current_prices", "No price data available.")
    consumption = state.get("recent_consumption", "No consumption data available.")

    user_content = (
        "## Historical Baseline (4-year summary)\n"
        f"{json.dumps(baseline, indent=2, default=str)}\n\n"
        "## Today's Price Forecast\n"
        f"{prices}\n\n"
        "## Recent Consumption (last ~48 hours)\n"
        f"{consumption}"
    )

    llm = get_llm()
    try:
        response = await llm.ainvoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=user_content),
            ]
        )
        analysis = extract_json_from_llm(response.content)
        logger.info(
            "Analysis complete: price_level=%s, percentile=%.1f",
            analysis.get("price_level", "?"),
            analysis.get("price_percentile", 0),
        )
        return {"analysis_json": json.dumps(analysis)}

    except Exception as exc:
        logger.error("Analysis failed: %s", exc, exc_info=True)
        return {"error": f"Analysis failed: {exc}"}
