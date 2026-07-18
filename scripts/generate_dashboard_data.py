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
    minimum_count = max(20, int(previous_count * 0.5)) if previous_count else 20
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
    validate(payload, previous)
    write_payload(payload)
    print(
        f"wrote {DATA_PATH} opportunities={len(payload['opportunities'])} "
        f"errors={len(payload.get('errors', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
