import asyncio
import logging
import sys
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.config import LOG_FILE, LOG_LEVEL
from src.graph import checkpoint_db_path, compile_graph


def setup_logging() -> None:
    log_path = Path(LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(sys.stdout),
        ],
    )


async def run_agent() -> None:
    logger = logging.getLogger("elis-agent")
    logger.info("=== Elis Agent starting ===")

    async with AsyncSqliteSaver.from_conn_string(checkpoint_db_path()) as checkpointer:
        graph = compile_graph(checkpointer)

        result = await graph.ainvoke(
            {},
            config={"configurable": {"thread_id": "elis-daily"}},
        )

        if result.get("error"):
            logger.error("Agent completed with error: %s", result["error"])
            sys.exit(1)

        logger.info("Agent completed successfully for %s", result.get("run_date", "unknown"))

        summary = result.get("summary_markdown", "")
        if summary:
            print("\n" + summary)


def main() -> None:
    setup_logging()
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        logging.getLogger("elis-agent").info("Interrupted by user")
        sys.exit(0)
    except Exception as exc:
        logging.getLogger("elis-agent").critical("Agent crashed: %s", exc, exc_info=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
