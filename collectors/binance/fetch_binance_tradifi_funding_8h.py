from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


BASE_URL = "https://fapi.binance.com"
EXCHANGE_INFO_URL = f"{BASE_URL}/fapi/v1/exchangeInfo"
FUNDING_RATE_URL = f"{BASE_URL}/fapi/v1/fundingRate"
FUNDING_INFO_URL = f"{BASE_URL}/fapi/v1/fundingInfo"

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
HISTORY_CSV = OUTPUT_DIR / "binance_tradifi_funding_8h.csv"
LATEST_CSV = OUTPUT_DIR / "binance_tradifi_funding_8h_latest.csv"
NORMALIZED_8H_CSV = OUTPUT_DIR / "binance_tradifi_funding_8h_normalized.csv"
SUMMARY_JSON = OUTPUT_DIR / "binance_tradifi_funding_8h.json"

UTC = dt.timezone.utc
BEIJING = dt.timezone(dt.timedelta(hours=8))
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 4
PAGE_LIMIT = 1000
DEFAULT_FUNDING_INTERVAL_HOURS = 8
INTERVAL_TOLERANCE_MINUTES = 5

HISTORY_FIELDS = [
    "symbol",
    "base_asset",
    "contract_type",
    "status",
    "onboard_time_utc",
    "onboard_time_beijing",
    "funding_time_utc",
    "funding_time_beijing",
    "funding_time_ms",
    "funding_rate",
    "funding_rate_pct",
    "mark_price",
    "configured_interval_hours",
    "interval_source",
    "elapsed_hours_since_previous",
    "is_expected_interval",
    "is_8h_interval",
]

LATEST_FIELDS = [
    "symbol",
    "base_asset",
    "status",
    "onboard_time_beijing",
    "configured_interval_hours",
    "interval_source",
    "funding_time_utc",
    "funding_time_beijing",
    "funding_rate",
    "funding_rate_pct",
    "mark_price",
    "records_total",
    "error",
]

NORMALIZED_8H_FIELDS = [
    "symbol",
    "base_asset",
    "window_end_utc",
    "window_end_beijing",
    "window_end_ms",
    "funding_rate_8h",
    "funding_rate_8h_pct",
    "settlement_count",
    "expected_settlement_count",
    "is_complete_by_current_interval",
    "configured_interval_hours",
    "first_settlement_time_utc",
    "last_settlement_time_utc",
    "last_mark_price",
]


def fetch_json(url: str, params: dict[str, Any] | None = None) -> Any:
    full_url = url
    if params:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"

    for attempt in range(1, MAX_RETRIES + 1):
        request = urllib.request.Request(
            full_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {418, 429, 500, 502, 503, 504}
            if not retryable or attempt == MAX_RETRIES:
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:300]}") from exc
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 2 ** (attempt - 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"request failed for {url}: {exc}") from exc
            delay = 2 ** (attempt - 1)
        time.sleep(delay)

    raise RuntimeError(f"request failed for {url}")


def timestamp_to_iso(value: Any, tz: dt.tzinfo) -> str:
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp_ms <= 0:
        return ""
    return dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=tz).isoformat()


def parse_cli_time(value: str, *, end_of_day: bool = False) -> dt.datetime:
    text = value.strip()
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end_of_day and len(text) == 10:
        parsed = parsed + dt.timedelta(days=1) - dt.timedelta(milliseconds=1)
    return parsed.astimezone(UTC)


def load_tradifi_contracts(selected_symbols: set[str]) -> list[dict[str, Any]]:
    exchange_info = fetch_json(EXCHANGE_INFO_URL)
    rows: list[dict[str, Any]] = []
    for item in exchange_info.get("symbols", []):
        symbol = str(item.get("symbol", "")).upper()
        if item.get("contractType") != "TRADIFI_PERPETUAL":
            continue
        if item.get("status") != "TRADING":
            continue
        if selected_symbols and symbol not in selected_symbols:
            continue
        rows.append(item)
    return sorted(rows, key=lambda item: str(item.get("symbol", "")))


def load_funding_intervals() -> dict[str, dict[str, Any]]:
    data = fetch_json(FUNDING_INFO_URL)
    if not isinstance(data, list):
        return {}
    return {str(item.get("symbol", "")).upper(): item for item in data}


def read_existing_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fetch_funding_history(
    symbol: str | None,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cursor = start_ms
    while cursor <= end_ms:
        params: dict[str, Any] = {
            "startTime": cursor,
            "endTime": end_ms,
            "limit": PAGE_LIMIT,
        }
        if symbol:
            params["symbol"] = symbol
        batch = fetch_json(FUNDING_RATE_URL, params)
        if not isinstance(batch, list) or not batch:
            break
        records.extend(batch)
        last_time = int(batch[-1]["fundingTime"])
        next_cursor = last_time + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < PAGE_LIMIT:
            break
        time.sleep(0.05)
    return records


def build_history_row(
    contract: dict[str, Any],
    record: dict[str, Any],
    interval_hours: int,
    interval_source: str,
) -> dict[str, Any]:
    funding_rate = str(record.get("fundingRate", ""))
    try:
        funding_rate_pct: float | str = float(funding_rate) * 100
    except ValueError:
        funding_rate_pct = ""
    onboard_ms = int(contract.get("onboardDate") or 0)
    funding_ms = int(record.get("fundingTime") or 0)
    return {
        "symbol": str(contract.get("symbol", "")),
        "base_asset": str(contract.get("baseAsset", "")),
        "contract_type": str(contract.get("contractType", "")),
        "status": str(contract.get("status", "")),
        "onboard_time_utc": timestamp_to_iso(onboard_ms, UTC),
        "onboard_time_beijing": timestamp_to_iso(onboard_ms, BEIJING),
        "funding_time_utc": timestamp_to_iso(funding_ms, UTC),
        "funding_time_beijing": timestamp_to_iso(funding_ms, BEIJING),
        "funding_time_ms": funding_ms,
        "funding_rate": funding_rate,
        "funding_rate_pct": funding_rate_pct,
        "mark_price": str(record.get("markPrice", "")),
        "configured_interval_hours": interval_hours,
        "interval_source": interval_source,
        "elapsed_hours_since_previous": "",
        "is_expected_interval": "",
        "is_8h_interval": "",
    }


def merge_and_annotate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        try:
            key = (str(row.get("symbol", "")).upper(), int(row.get("funding_time_ms") or 0))
        except (TypeError, ValueError):
            continue
        if key[0] and key[1] > 0:
            unique[key] = row

    merged = [unique[key] for key in sorted(unique)]
    previous_by_symbol: dict[str, int] = {}
    for row in merged:
        symbol = str(row["symbol"])
        funding_ms = int(row["funding_time_ms"])
        previous_ms = previous_by_symbol.get(symbol)
        if previous_ms is not None:
            elapsed_hours = (funding_ms - previous_ms) / 3_600_000
            configured_hours = float(row.get("configured_interval_hours") or DEFAULT_FUNDING_INTERVAL_HOURS)
            tolerance_hours = INTERVAL_TOLERANCE_MINUTES / 60
            row["elapsed_hours_since_previous"] = round(elapsed_hours, 6)
            row["is_expected_interval"] = abs(elapsed_hours - configured_hours) <= tolerance_hours
            row["is_8h_interval"] = abs(elapsed_hours - 8) <= tolerance_hours
        else:
            row["elapsed_hours_since_previous"] = ""
            row["is_expected_interval"] = ""
            row["is_8h_interval"] = ""
        previous_by_symbol[symbol] = funding_ms
    return merged


def latest_rows(
    contracts: list[dict[str, Any]],
    history: list[dict[str, Any]],
    intervals: dict[str, dict[str, Any]],
    errors: dict[str, str],
) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in history:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)

    result: list[dict[str, Any]] = []
    for contract in contracts:
        symbol = str(contract.get("symbol", ""))
        records = by_symbol.get(symbol, [])
        latest = records[-1] if records else {}
        info = intervals.get(symbol, {})
        configured_interval = int(info.get("fundingIntervalHours") or DEFAULT_FUNDING_INTERVAL_HOURS)
        result.append(
            {
                "symbol": symbol,
                "base_asset": str(contract.get("baseAsset", "")),
                "status": str(contract.get("status", "")),
                "onboard_time_beijing": timestamp_to_iso(contract.get("onboardDate"), BEIJING),
                "configured_interval_hours": configured_interval,
                "interval_source": "fundingInfo" if symbol in intervals else "default_8h",
                "funding_time_utc": latest.get("funding_time_utc", ""),
                "funding_time_beijing": latest.get("funding_time_beijing", ""),
                "funding_rate": latest.get("funding_rate", ""),
                "funding_rate_pct": latest.get("funding_rate_pct", ""),
                "mark_price": latest.get("mark_price", ""),
                "records_total": len(records),
                "error": errors.get(symbol, ""),
            }
        )
    return result


def normalize_to_8h_windows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    period_ms = 8 * 60 * 60 * 1000
    tolerance_ms = INTERVAL_TOLERANCE_MINUTES * 60 * 1000
    groups: dict[tuple[str, int], dict[str, Any]] = {}

    for row in history:
        symbol = str(row.get("symbol", ""))
        try:
            funding_ms = int(row.get("funding_time_ms") or 0)
            rate = Decimal(str(row.get("funding_rate", "0")))
        except (TypeError, ValueError, InvalidOperation):
            continue

        floor_boundary = (funding_ms // period_ms) * period_ms
        if funding_ms - floor_boundary <= tolerance_ms:
            window_end_ms = floor_boundary
        else:
            window_end_ms = floor_boundary + period_ms

        key = (symbol, window_end_ms)
        group = groups.setdefault(
            key,
            {
                "symbol": symbol,
                "base_asset": row.get("base_asset", ""),
                "window_end_ms": window_end_ms,
                "rate": Decimal("0"),
                "settlement_count": 0,
                "configured_interval_hours": int(
                    float(row.get("configured_interval_hours") or DEFAULT_FUNDING_INTERVAL_HOURS)
                ),
                "first_settlement_ms": funding_ms,
                "last_settlement_ms": funding_ms,
                "last_mark_price": row.get("mark_price", ""),
            },
        )
        group["rate"] += rate
        group["settlement_count"] += 1
        group["first_settlement_ms"] = min(group["first_settlement_ms"], funding_ms)
        if funding_ms >= group["last_settlement_ms"]:
            group["last_settlement_ms"] = funding_ms
            group["last_mark_price"] = row.get("mark_price", "")

    rows: list[dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        configured_interval = group["configured_interval_hours"]
        expected_count = max(1, round(8 / configured_interval))
        rate = group["rate"]
        rows.append(
            {
                "symbol": group["symbol"],
                "base_asset": group["base_asset"],
                "window_end_utc": timestamp_to_iso(group["window_end_ms"], UTC),
                "window_end_beijing": timestamp_to_iso(group["window_end_ms"], BEIJING),
                "window_end_ms": group["window_end_ms"],
                "funding_rate_8h": str(rate),
                "funding_rate_8h_pct": str(rate * Decimal("100")),
                "settlement_count": group["settlement_count"],
                "expected_settlement_count": expected_count,
                "is_complete_by_current_interval": group["settlement_count"] >= expected_count,
                "configured_interval_hours": configured_interval,
                "first_settlement_time_utc": timestamp_to_iso(group["first_settlement_ms"], UTC),
                "last_settlement_time_utc": timestamp_to_iso(group["last_settlement_ms"], UTC),
                "last_mark_price": group["last_mark_price"],
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch every Binance TradFi perpetual funding settlement record."
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=[],
        help="Optional symbols, for example: --symbols MUBUSDT TSLAUSDT",
    )
    parser.add_argument(
        "--start-time",
        help="UTC ISO time for a lower bound. Defaults to each contract onboard time.",
    )
    parser.add_argument(
        "--end-time",
        help="UTC ISO time for an upper bound. Defaults to now.",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Ignore the existing history CSV and download again.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    selected_symbols = {value.upper() for value in args.symbols}
    contracts = load_tradifi_contracts(selected_symbols)
    if not contracts:
        raise RuntimeError("no active Binance TRADIFI_PERPETUAL contracts found")

    intervals = load_funding_intervals()
    end_time = parse_cli_time(args.end_time, end_of_day=True) if args.end_time else dt.datetime.now(UTC)
    requested_start = parse_cli_time(args.start_time) if args.start_time else None
    existing = [] if args.full_refresh else read_existing_history(HISTORY_CSV)

    last_time_by_symbol: dict[str, int] = {}
    for row in existing:
        try:
            symbol = str(row.get("symbol", "")).upper()
            funding_ms = int(row.get("funding_time_ms") or 0)
        except (TypeError, ValueError):
            continue
        last_time_by_symbol[symbol] = max(last_time_by_symbol.get(symbol, 0), funding_ms)

    contract_by_symbol = {
        str(contract.get("symbol", "")): contract for contract in contracts
    }
    end_ms = int(end_time.timestamp() * 1000)
    start_by_symbol: dict[str, int] = {}
    for symbol, contract in contract_by_symbol.items():
        onboard_ms = int(contract.get("onboardDate") or 0)
        start_ms = int(requested_start.timestamp() * 1000) if requested_start else onboard_ms
        if not args.full_refresh and symbol in last_time_by_symbol:
            start_ms = max(start_ms, last_time_by_symbol[symbol] + 1)
        start_by_symbol[symbol] = start_ms

    records_by_symbol: dict[str, list[dict[str, Any]]] = {
        symbol: [] for symbol in contract_by_symbol
    }
    errors: dict[str, str] = {}
    print(f"active_tradifi_contracts={len(contracts)}")

    incremental_symbols = [
        symbol
        for symbol in contract_by_symbol
        if symbol in last_time_by_symbol and start_by_symbol[symbol] <= end_ms
    ]
    if incremental_symbols:
        bulk_start_ms = min(start_by_symbol[symbol] for symbol in incremental_symbols)
        try:
            bulk_records = fetch_funding_history(None, bulk_start_ms, end_ms)
            incremental_set = set(incremental_symbols)
            for record in bulk_records:
                symbol = str(record.get("symbol", ""))
                funding_ms = int(record.get("fundingTime") or 0)
                if symbol in incremental_set and funding_ms >= start_by_symbol[symbol]:
                    records_by_symbol[symbol].append(record)
            print(
                f"bulk_incremental_records={len(bulk_records)} "
                f"tradifi_records={sum(len(records_by_symbol[s]) for s in incremental_symbols)}"
            )
        except Exception as exc:
            message = f"bulk incremental request failed: {exc}"
            for symbol in incremental_symbols:
                errors[symbol] = message
            print(message, file=sys.stderr)

    initial_symbols = [
        symbol
        for symbol in contract_by_symbol
        if symbol not in last_time_by_symbol and start_by_symbol[symbol] <= end_ms
    ]
    for index, symbol in enumerate(initial_symbols, start=1):
        try:
            records_by_symbol[symbol] = fetch_funding_history(
                symbol,
                start_by_symbol[symbol],
                end_ms,
            )
            print(f"[initial {index:03d}/{len(initial_symbols):03d}] {symbol}: records={len(records_by_symbol[symbol])}")
        except Exception as exc:
            errors[symbol] = str(exc)
            print(
                f"[initial {index:03d}/{len(initial_symbols):03d}] {symbol}: error={exc}",
                file=sys.stderr,
            )
        time.sleep(0.03)

    new_rows: list[dict[str, Any]] = []
    for symbol, contract in contract_by_symbol.items():
        info = intervals.get(symbol, {})
        configured_interval = int(info.get("fundingIntervalHours") or DEFAULT_FUNDING_INTERVAL_HOURS)
        interval_source = "fundingInfo" if symbol in intervals else "default_8h"
        for record in records_by_symbol[symbol]:
            new_rows.append(
                build_history_row(contract, record, configured_interval, interval_source)
            )

    history = merge_and_annotate(existing + new_rows)
    latest = latest_rows(contracts, history, intervals, errors)
    normalized_8h = normalize_to_8h_windows(history)
    write_csv(HISTORY_CSV, HISTORY_FIELDS, history)
    write_csv(LATEST_CSV, LATEST_FIELDS, latest)
    write_csv(NORMALIZED_8H_CSV, NORMALIZED_8H_FIELDS, normalized_8h)

    generated_at = dt.datetime.now(UTC)
    summary = {
        "generated_at_utc": generated_at.isoformat(),
        "generated_at_beijing": generated_at.astimezone(BEIJING).isoformat(),
        "sources": {
            "contracts": EXCHANGE_INFO_URL,
            "funding_history": FUNDING_RATE_URL,
            "funding_interval_adjustments": FUNDING_INFO_URL,
        },
        "filters": "contractType=TRADIFI_PERPETUAL and status=TRADING",
        "note": "Positive fundingRate means longs pay shorts. fundingInfo only lists adjusted symbols; absent symbols use the normal 8-hour interval.",
        "active_contract_count": len(contracts),
        "history_record_count": len(history),
        "normalized_8h_record_count": len(normalized_8h),
        "new_record_count": len(new_rows),
        "error_count": len(errors),
        "errors": errors,
        "latest": latest,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"history_record_count={len(history)}")
    print(f"normalized_8h_record_count={len(normalized_8h)}")
    print(f"new_record_count={len(new_rows)}")
    print(f"error_count={len(errors)}")
    print(f"wrote {HISTORY_CSV.name}")
    print(f"wrote {LATEST_CSV.name}")
    print(f"wrote {NORMALIZED_8H_CSV.name}")
    print(f"wrote {SUMMARY_JSON.name}")
    return 1 if errors and len(errors) == len(contracts) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
