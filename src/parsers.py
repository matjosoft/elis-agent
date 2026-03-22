import json
import re


def parse_historic_json(result) -> list[dict]:
    """
    Parse the output from the get-historic-json MCP tool.

    Expected input (list of pytibber nodes):
        [
          {"from": "2022-04-01T00:00:00+02:00", "to": "...",
           "consumption": 1.23, "cost": 0.45,
           "unitPrice": 0.36, "unitPriceVAT": 0.09},
          ...
        ]

    Returns a list of dicts ready for insert_consumption_batch().
    """
    if isinstance(result, str):
        # MCP tool may return a JSON string or wrap it in content blocks
        data = _extract_json_value(result)
    else:
        data = result

    if not isinstance(data, list):
        return []

    records = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("consumption") is None:
            continue
        records.append(
            {
                "timestamp": entry["from"],
                "consumption_kwh": entry.get("consumption"),
                "cost": entry.get("cost"),
                "unit_price": entry.get("unitPrice"),
                "unit_price_vat": entry.get("unitPriceVAT"),
            }
        )
    return records


def parse_production_json(result) -> list[dict]:
    """
    Parse the output from get-historic-json with production=True.

    Expected input (list of pytibber nodes):
        [
          {"from": "2024-01-01T00:00:00+01:00", "to": "...",
           "production": 0.75, "profit": 0.31,
           "unitPrice": 0.41, "unitPriceVAT": 0.10},
          ...
        ]

    Returns a list of dicts ready for insert_production_batch().
    """
    if isinstance(result, str):
        data = _extract_json_value(result)
    else:
        data = result

    if not isinstance(data, list):
        return []

    records = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("production") is None:
            continue
        records.append(
            {
                "timestamp": entry["from"],
                "production_kwh": entry.get("production"),
                "profit": entry.get("profit"),
                "unit_price": entry.get("unitPrice"),
                "unit_price_vat": entry.get("unitPriceVAT"),
            }
        )
    return records


def parse_home_id(text: str) -> str:
    """
    Extract the first home ID from the list-homes text response.

    The response format is:
        Home: My House
        ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        ...
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("id:"):
            home_id = stripped.split(":", 1)[1].strip()
            if home_id:
                return home_id
    raise ValueError(f"Could not parse home_id from list-homes response: {text[:300]}")


def extract_json_from_llm(text: str) -> dict:
    """
    Extract a JSON object from an LLM response.
    Handles raw JSON, markdown code fences, and mixed prose.
    """
    # 1. Try markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # 2. Try the whole text as JSON
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 3. Find first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError(f"Could not extract JSON from LLM response: {text[:300]}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_json_value(text: str):
    """
    Robustly parse JSON from a string that may contain surrounding text
    or be a plain JSON array/object.
    """
    text = text.strip()
    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Look for a JSON array
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    return []
