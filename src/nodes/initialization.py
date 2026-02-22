import logging
from datetime import date

from langchain_mcp_adapters.client import MultiServerMCPClient

from src.config import get_mcp_client_config
from src.parsers import parse_home_id
from src.state import AgentState

logger = logging.getLogger(__name__)


async def initialization_node(state: AgentState) -> dict:
    """
    Connect to the Tibber MCP server, discover tools, and get the home_id.
    """
    try:
        client = MultiServerMCPClient(get_mcp_client_config())
        tools = await client.get_tools()

        list_homes = next((t for t in tools if t.name == "list-homes"), None)
        if list_homes is None:
            raise RuntimeError("list-homes tool not found in MCP server")

        result = await list_homes.ainvoke({})
        # result may be a string or a list of content objects
        text = _to_text(result)
        home_id = parse_home_id(text)

        logger.info("Initialized: home_id=%s, %d tools available", home_id, len(tools))

        return {
            "home_id": home_id,
            "run_date": date.today().isoformat(),
            "error": None,
        }
    except Exception as exc:
        logger.error("Initialization failed: %s", exc, exc_info=True)
        return {"error": f"Initialization failed: {exc}"}


def _to_text(result) -> str:
    """Normalise a tool invocation result to plain text."""
    if isinstance(result, str):
        return result
    # LangChain wraps tool output in a list of content objects
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
