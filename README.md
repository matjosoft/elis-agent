# Technical Specification: Autonomous Energy Analyst (with Historical Seeding)

## 1. Project Overview

An autonomous, stateful AI agent for **Raspberry Pi**. It features a one-time "Seeding" process to ingest 4 years of historical data, followed by a daily LangGraph-driven analysis of electricity prices and consumption via **Tibber MCP** and **OpenRouter**.

## 2. Technical Stack

* **Orchestration:** LangGraph (Python) for the daily analytical loops.
* **Integration:** Tibber MCP Server (`https://github.com/corroleaus/tibber-mcp`) via Python `mcp` SDK.
* **Database:** **SQLite** (using `sqlite3` and LangGraph `SqliteSaver`). SQLite is critical here to store the 4-year historical dataset locally on the Pi.
* **Brain (Intelligence):** OpenRouter (ChatGPT-5-mini or simmilar).

## 3. The Seeding Module (First-Run Logic)

Since Tibber's API may limit the amount of data returned in a single request, the agent must implement a "chunked" fetch strategy for the initial SEED

### A. Seeding Requirements:

* **Start Date:** 2022-04-01
* **Resolution:** Hourly consumption data.
* **Chunking:** Fetch data in monthly or quarterly chunks to prevent timeouts.
* **Checkpointing:** Store the "Last Fetched Timestamp" in SQLite. If the process is interrupted (common on a Pi), it must resume from where it left off.
* **Schema:** A `consumption_history` table in SQLite with columns: `timestamp`, `consumption_kwh`, `cost`, and `unit_price`.

## 4. LangGraph Architecture

### Nodes & Workflow:

1. **`initialization_node`**: Establishes connection to the MCP server and discovers available tools.
2. **`check_seed_status_node`**: Checks if historical data exists in SQLite. If not, it triggers the `seeding_node`.
3. **`seeding_node`**: Loops through the history data, calling the Tibber MCP tool `get_consumption` repeatedly until the database is populated.
4. **`fetch_daily_node`**: Once seeded, it only fetches "Delta" data (from the last run until now) plus today's spot prices.
5. **`analysis_node`**: The LLM compares current prices against the 4-year historical baseline (e.g., "Today's price is in the 90th percentile of the last 4 years").
* Identification of peak/off-peak price correlations.
* Calculate savings from 15 minute prices versus paying for average daily price
* Anomalies in consumption compared to previous days.
* Actionable recommendations (e.g., "Shift EV charging to 0
6. **`storage_node`**: Commits the analysis to a local SQLite database to track historical trends.
7. **`reporting_node`**: Generates a final Markdown summary for the user.



## 5. Data Model & Persistence

The `State` must now handle a much larger context:

* **`is_seeded`**: Boolean flag.
* **`baseline_metrics`**: A summary of the 4-year data (average consumption per month/hour) so the LLM doesn't have to process millions of rows every time.
* **`current_context`**: Today's prices and yesterday's usage.

## 6. Execution Instructions for the AI Coder
## Instructions for the AI Coder

1. **Setup:** Create a Python project using `langgraph`, `mcp`, `langchain-openai`, and `python-dotenv`.
2. **MCP Wrapper:** Implement a context manager to handle the lifecycle of the `stdio_client` for the Tibber MCP server.
3. **Graph Construction:** Define the nodes and edges in LangGraph. Ensure the graph supports a `SqliteSaver` checkpointer.
4. **Prompt Engineering:** Design a system prompt that encourages the AI to act as a "Senior Energy Strategist" with access to real-time tools.
5. **Output:** Generate a `main.py` that can be executed as a cron job or a persistent service.
6. **Database Setup:** Create a robust SQLite schema to handle ~35,000 rows (4 years of hourly data). Index the `timestamp` column.
7. **Rate Limit Handling:** Add a `time.sleep(1)` ,when seeding, between chunks during seeding to respect Tibber's API limits.


## 6. Core Components
### A. Tibber MCP Client

The agent must interface with the Tibber MCP server using a `stdio` connection.

* **MCP** https://github.com/corroleaus/tibber-mcp
* **Available tools** 
list-homes: List all Tibber homes and their basic information
get-consumption: Get energy consumption data for a specific home
get-production: Get energy production data for a specific home
get-price-info: Get current and upcoming electricity prices
get-realtime: Get latest real-time power readings
get-historic: Get historical data with custom resolution
get-price-forecast: Get detailed price forecasts for today and tomorrow
* **Auth:** Requires `TIBBER_TOKEN` injected via environment variables.

### C. OpenRouter Integration
* Use the `langchain-openai` wrapper or `httpx` directly.
* **Base URL:** `https://openrouter.ai/api/v1`
* **State Handling:** Ensure the model is instructed to provide structured JSON outputs for easier parsing into the database.


## 7. Deployment Requirements (Raspberry Pi)

* **Environment:** Use a `.env` file for `TIBBER_TOKEN`, `OPENROUTER_API_KEY`, and `PORT`.
* **Logging:** Output logs to `agent.log` for headless debugging on the Pi.