from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent.parent


class ValidationError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"expected a JSON object: {path}")
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as file:
            return list(csv.DictReader(file))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing CSV file: {path}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def error_mentions(errors: Any, exchanges: set[str]) -> list[str]:
    if not isinstance(errors, list):
        return ["errors is not a list"]
    return [
        str(error)
        for error in errors
        if any(exchange in str(error).lower() for exchange in exchanges)
    ]


def opportunity_has_funding(item: dict[str, Any], exchange: str) -> bool:
    latest = item.get("latest")
    if not isinstance(latest, dict):
        return False
    value = latest.get(exchange)
    return (
        isinstance(value, dict)
        and value.get("rate_pct") is not None
        and bool(value.get("time"))
        and not value.get("error")
    )


def opportunity_has_turnover(item: dict[str, Any], exchange: str) -> bool:
    turnover = item.get("turnover_24h_usdt")
    if not isinstance(turnover, dict):
        return False
    try:
        return float(turnover.get(exchange) or 0) > 0
    except (TypeError, ValueError):
        return False


def validate(project_dir: Path) -> dict[str, Any]:
    dashboard = load_json(project_dir / "data" / "dashboard.json")
    opportunities = dashboard.get("opportunities")
    require(
        isinstance(opportunities, list) and len(opportunities) > 0,
        "dashboard opportunities must contain at least one record",
    )
    typed_opportunities = [item for item in opportunities if isinstance(item, dict)]

    dashboard_errors = error_mentions(
        dashboard.get("errors", []),
        {"binance", "bybit"},
    )
    require(
        not dashboard_errors,
        f"dashboard errors contain Binance/Bybit failures: {dashboard_errors}",
    )

    turnover_metadata = dashboard.get("turnover_metadata")
    require(
        isinstance(turnover_metadata, dict),
        "dashboard turnover_metadata is missing",
    )
    turnover_errors = error_mentions(
        turnover_metadata.get("errors", []),
        {"binance", "bybit"},
    )
    require(
        not turnover_errors,
        f"turnover metadata contains Binance/Bybit failures: {turnover_errors}",
    )

    exchange_counts: dict[str, dict[str, int]] = {}
    for exchange in ("binance", "bybit"):
        exchange_opportunities = [
            item
            for item in typed_opportunities
            if exchange in item.get("exchanges", [])
        ]
        funding_count = sum(
            opportunity_has_funding(item, exchange)
            for item in exchange_opportunities
        )
        turnover_count = sum(
            opportunity_has_turnover(item, exchange)
            for item in exchange_opportunities
        )
        require(
            funding_count > 0,
            f"{exchange} has no valid funding data in dashboard opportunities",
        )
        require(
            turnover_count > 0,
            f"{exchange} has no positive turnover data in dashboard opportunities",
        )
        exchange_counts[exchange] = {
            "opportunities": len(exchange_opportunities),
            "funding": funding_count,
            "turnover": turnover_count,
        }

    turnover_cache = load_json(project_dir / "scripts" / "turnover_24h.json")
    venues = turnover_cache.get("venues")
    require(isinstance(venues, dict), "turnover cache venues is missing")
    for venue in ("binance_spot", "binance_perp", "bybit_spot", "bybit_perp"):
        require(
            isinstance(venues.get(venue), dict) and len(venues[venue]) > 0,
            f"turnover cache venue is empty: {venue}",
        )
    cache_errors = error_mentions(
        turnover_cache.get("errors", []),
        {"binance", "bybit"},
    )
    require(
        not cache_errors,
        f"turnover cache contains Binance/Bybit failures: {cache_errors}",
    )

    binance_summary = load_json(
        project_dir
        / "collectors"
        / "binance"
        / "outputs"
        / "binance_tradifi_funding_8h.json"
    )
    require(
        int(binance_summary.get("active_contract_count") or 0) > 0,
        "Binance active contract count is zero",
    )
    require(
        int(binance_summary.get("history_record_count") or 0) > 0,
        "Binance funding history is empty",
    )
    require(
        int(binance_summary.get("error_count") or 0) == 0,
        f"Binance collector errors: {binance_summary.get('errors')}",
    )

    bybit_summary = load_json(
        project_dir
        / "collectors"
        / "multi_exchange"
        / "outputs"
        / "bybit_tradifi_summary.json"
    )
    require("fatal_error" not in bybit_summary, "Bybit summary contains fatal_error")
    require(
        int(bybit_summary.get("error_count") or 0) == 0,
        f"Bybit collector errors: {bybit_summary.get('errors')}",
    )
    require(
        int(bybit_summary.get("spot_count") or 0) > 0,
        "Bybit xStocks spot count is zero",
    )
    require(
        int(bybit_summary.get("perp_count") or 0) > 0,
        "Bybit perpetual count is zero",
    )
    require(
        int(bybit_summary.get("funding_record_count") or 0) > 0,
        "Bybit funding history is empty",
    )

    bybit_markets = read_csv(
        project_dir
        / "collectors"
        / "multi_exchange"
        / "outputs"
        / "bybit_tradifi_markets.csv"
    )
    xstocks = [
        row
        for row in bybit_markets
        if row.get("market") == "spot" and row.get("category") == "stock"
    ]
    require(xstocks, "Bybit markets output contains no xStocks spot records")

    return {
        "generated_at": dashboard.get("generated_at", ""),
        "opportunities": len(opportunities),
        "binance": {
            **exchange_counts["binance"],
            "active_contracts": int(
                binance_summary.get("active_contract_count") or 0
            ),
            "funding_records": int(
                binance_summary.get("history_record_count") or 0
            ),
        },
        "bybit": {
            **exchange_counts["bybit"],
            "spot_records": int(bybit_summary.get("spot_count") or 0),
            "perp_records": int(bybit_summary.get("perp_count") or 0),
            "funding_records": int(
                bybit_summary.get("funding_record_count") or 0
            ),
            "xstocks_spot_records": len(xstocks),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate server-generated TradFi dashboard data"
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=PROJECT_DIR,
    )
    args = parser.parse_args()

    try:
        summary = validate(args.project_dir.resolve())
    except ValidationError as exc:
        print(f"VALIDATION FAILED: {exc}")
        return 1

    print("VALIDATION PASSED")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
