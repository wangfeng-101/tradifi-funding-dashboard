from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_PATH = PROJECT_DIR / "data" / "dashboard.json"

sys.path.insert(0, str(SCRIPT_DIR))

from dashboard_core import build_payload  # noqa: E402
from turnover import refresh_turnover_cache  # noqa: E402


def read_previous() -> dict[str, Any] | None:
    if not DATA_PATH.exists():
        return None
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def opportunity_exchanges(opportunity: dict[str, Any]) -> set[str]:
    values = opportunity.get("exchanges", [])
    if not isinstance(values, list):
        return set()
    return {str(value).lower() for value in values if value}


def missing_file_exchanges(payload: dict[str, Any]) -> set[str]:
    exchanges = payload.get("exchanges", {})
    known = set(exchanges) if isinstance(exchanges, dict) else set()
    missing: set[str] = set()
    for error in payload.get("errors", []):
        message = str(error)
        if "No such file or directory" not in message:
            continue
        prefix = message.split(maxsplit=1)[0].lower()
        if prefix in known:
            missing.add(prefix)
    return missing


def preserve_missing_exchange_data(
    payload: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    if not previous:
        return payload

    missing = missing_file_exchanges(payload)
    if not missing:
        return payload

    current = [
        item
        for item in payload.get("opportunities", [])
        if isinstance(item, dict) and not (opportunity_exchanges(item) & missing)
    ]
    preserved = [
        item
        for item in previous.get("opportunities", [])
        if isinstance(item, dict) and opportunity_exchanges(item) & missing
    ]

    by_id: dict[str, dict[str, Any]] = {}
    for item in current + preserved:
        identifier = str(item.get("id", ""))
        if identifier:
            by_id[identifier] = item

    payload["opportunities"] = sorted(
        by_id.values(),
        key=lambda item: (
            str(item.get("strategy_type", "")),
            str(item.get("underlying", "")),
            str(item.get("pair_id", "")),
        ),
    )
    payload["stale_exchanges"] = sorted(missing)
    payload.setdefault("errors", []).append(
        "using previous opportunity data for missing exchanges: "
        + ", ".join(sorted(missing))
    )
    return payload


def validate(payload: dict[str, Any], previous: dict[str, Any] | None) -> None:
    opportunities = payload.get("opportunities")
    if not isinstance(opportunities, list) or not opportunities:
        raise RuntimeError("generated payload has no opportunities; keeping the previous file")

    required_strategies = {"same_spot_perp", "cross_spot_perp", "cross_perp"}
    available_strategies = {
        item.get("strategy_type")
        for item in opportunities
        if isinstance(item, dict)
    }
    missing = required_strategies - available_strategies
    if missing:
        raise RuntimeError(
            f"generated payload is missing strategies {sorted(missing)}; keeping the previous file"
        )

    previous_count = len(previous.get("opportunities", [])) if previous else 0
    minimum_count = max(20, int(previous_count * 0.8)) if previous_count else 20
    if len(opportunities) < minimum_count:
        raise RuntimeError(
            f"opportunity count dropped from {previous_count} to {len(opportunities)}; "
            "keeping the previous file"
        )


def write_payload(payload: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(DATA_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the static TradFi dashboard JSON")
    parser.add_argument(
        "--refresh-turnover",
        action="store_true",
        help="Refresh public 24h turnover tickers before building",
    )
    args = parser.parse_args()

    if args.refresh_turnover:
        turnover = refresh_turnover_cache()
        print(
            f"turnover venues={len(turnover.get('venues', {}))} "
            f"errors={len(turnover.get('errors', []))}"
        )

    previous = read_previous()
    payload = build_payload()
    payload = preserve_missing_exchange_data(payload, previous)
    validate(payload, previous)
    write_payload(payload)
    print(
        f"wrote {DATA_PATH} opportunities={len(payload['opportunities'])} "
        f"errors={len(payload.get('errors', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
