from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .dashboard_core import CONFIG_PATH, load_market_sources, read_json
except ImportError:
    from dashboard_core import CONFIG_PATH, load_market_sources, read_json


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "data" / "funding"


def build_funding_series_payloads(
    markets: dict[str, dict[str, Any]],
    generated_at: str,
) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for exchange, exchange_markets in markets.items():
        series: dict[str, list[list[float | int]]] = {}
        for future in exchange_markets.get("futures", {}).values():
            symbol = str(future.get("symbol") or "").strip()
            records = future.get("records") or []
            if not symbol or not records:
                continue
            series[symbol] = [
                [int(timestamp.timestamp() * 1000), rate_pct]
                for timestamp, rate_pct in records
            ]

        if series:
            payloads[exchange] = {
                "exchange": exchange,
                "generated_at": generated_at,
                "series": dict(sorted(series.items())),
            }
    return payloads


def load_funding_series_payloads(
    generated_at: str | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    config = read_json(CONFIG_PATH)
    errors: list[str] = []
    markets = load_market_sources(config, errors)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    return build_funding_series_payloads(markets, timestamp), errors


def write_funding_series_payloads(
    payloads: dict[str, dict[str, Any]],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for exchange, payload in payloads.items():
        target = output_dir / f"{exchange}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(target)
