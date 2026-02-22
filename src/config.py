import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
TIBBER_TOKEN: str = os.environ["TIBBER_TOKEN"]
OPENROUTER_API_KEY: str = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

# --- Paths ---
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
TIBBER_MCP_PATH: str = os.getenv(
    "TIBBER_MCP_PATH",
    str(PROJECT_ROOT.parent / "develop1/mcp/tibber-mcp"),
)
DB_PATH: str = os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "elis.db"))
REPORTS_PATH: str = os.getenv("REPORTS_PATH", str(PROJECT_ROOT / "reports"))
LOG_FILE: str = os.getenv("LOG_FILE", str(PROJECT_ROOT / "agent.log"))

# --- Seeding ---
SEED_START_DATE: str = "2022-04-01"
SEED_CHUNK_HOURS: int = 744  # max hours in a month (31 * 24)
SEED_RATE_LIMIT_SECONDS: float = 1.0

# --- Logging ---
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


def get_llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        model=OPENROUTER_MODEL,
        temperature=0.3,
    )


def get_mcp_client_config() -> dict:
    return {
        "tibber": {
            "command": "uv",
            "args": ["--directory", TIBBER_MCP_PATH, "run", "tibber-mcp"],
            "transport": "stdio",
            "env": {"TIBBER_TOKEN": TIBBER_TOKEN},
        }
    }
