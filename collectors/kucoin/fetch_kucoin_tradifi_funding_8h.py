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


BASE_URL = "https://api-futures.kucoin.com"
ACTIVE_CONTRACTS_URL = f"{BASE_URL}/api/v1/contracts/active"
FUNDING_HISTORY_URL = f"{BASE_URL}/api/v1/contract/funding-rates"

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
HISTORY_CSV = OUTPUT_DIR / "kucoin_tradifi_funding_8h.csv"
LATEST_CSV = OUTPUT_DIR / "kucoin_tradifi_funding_8h_latest.csv"
NORMALIZED_8H_CSV = OUTPUT_DIR / "kucoin_tradifi_funding_8h_normalized.csv"
SUMMARY_JSON = OUTPUT_DIR / "kucoin_tradifi_funding_8h.json"

UTC = dt.timezone.utc
BEIJING = dt.timezone(dt.timedelta(hours=8))
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 4
MAX_QUERY_WINDOW = dt.timedelta(days=30)
DEFAULT_FUNDING_INTERVAL_HOURS = 8.0
INTERVAL_TOLERANCE_MINUTES = 5

HISTORY_FIELDS = [
    "symbol",
    "base_currency",
    "market_type",
    "status",
    "first_open_time_utc",
    "first_open_time_beijing",
    "funding_time_utc",
    "funding_time_beijing",
    "funding_time_ms",
    "funding_rate",
    "funding_rate_pct",
    "configured_interval_hours",
    "interval_source",
    "elapsed_hours_since_previous",
    "is_expected_interval",
    "is_8h_interval",
]

LATEST_FIELDS = [
    "symbol",
    "base_currency",
    "market_type",
    "status",
    "first_open_time_beijing",
    "configured_interval_hours",
    "interval_source",
    "funding_time_utc",
    "funding_time_beijing",
    "funding_rate",
    "funding_rate_pct",
    "current_mark_price",
    "records_total",
    "error",
]

NORMALIZED_8H_FIELDS = [
    "symbol",
    "base_currency",
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
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("code") == "200000":
                return payload
            code = str(payload.get("code", ""))
            if code not in {"429000", "200002"} or attempt == MAX_RETRIES:
                raise RuntimeError(f"KuCoin API error from {url}: {payload}")
            delay = 2 ** (attempt - 1)
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {429, 500, 502, 503, 504}
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


def timestamp_to_iso(value: Any, timezone: dt.tzinfo) -> str:
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp_ms <= 0:
        return ""
    return dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone).isoformat()


def parse_cli_time(value: str, *, end_of_day: bool = False) -> dt.datetime:
    text = value.strip()
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end_of_day and len(text) == 10:
        parsed = parsed + dt.timedelta(days=1) - dt.timedelta(milliseconds=1)
    return parsed.astimezone(UTC)


def is_tradifi_contract(item: dict[str, Any]) -> bool:
    return (
        str(item.get("status", "")) == "Open"
        and str(item.get("marketType", "")).upper() not in {"", "CRYPTO"}
        and str(item.get("quoteCurrency", "")) == "USDT"
        and str(item.get("settleCurrency", "")) == "USDT"
    )


def load_tradifi_contracts(selected_symbols: set[str]) -> list[dict[str, Any]]:
    payload = fetch_json(ACTIVE_CONTRACTS_URL)
    data = payload.get("data", [])
    if isinstance(data, dict):
        data = [data]
    contracts = []
    for item in data:
        symbol = str(item.get("symbol", "")).upper()
        if not is_tradifi_contract(item):
            continue
        if selected_symbols and symbol not in selected_symbols:
            continue
        contracts.append(item)
    return sorted(contracts, key=lambda item: str(item.get("symbol", "")))


def configured_interval(contract: dict[str, Any]) -> tuple[float, str]:
    current = contract.get("currentFundingRateGranularity")
    original = contract.get("fundingRateGranularity")
    for value, source in (
        (current, "currentFundingRateGranularity"),
        (original, "fundingRateGranularity"),
    ):
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            continue
        if milliseconds > 0:
            return milliseconds / 3_600_000, source
    return DEFAULT_FUNDING_INTERVAL_HOURS, "default_8h"


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


def fetch_funding_history(symbol: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cursor = dt.datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    end = dt.datetime.fromtimestamp(end_ms / 1000, tz=UTC)

    while cursor <= end:
        chunk_end = min(cursor + MAX_QUERY_WINDOW, end)
        payload = fetch_json(
            FUNDING_HISTORY_URL,
            {
                "symbol": symbol,
                "from": int(cursor.timestamp() * 1000),
                "to": int(chunk_end.timestamp() * 1000),
            },
        )
        data = payload.get("data", [])
        if isinstance(data, list):
            records.extend(data)
        cursor = chunk_end + dt.timedelta(milliseconds=1)
        time.sleep(0.03)

    unique: dict[int, dict[str, Any]] = {}
    for item in records:
        try:
            unique[int(item["timepoint"])] = item
        except (KeyError, TypeError, ValueError):
            continue
    return [unique[key] for key in sorted(unique)]


def build_history_row(
    contract: dict[str, Any],
    record: dict[str, Any],
    interval_hours: float,
    interval_source: str,
) -> dict[str, Any]:
    funding_rate = str(record.get("fundingRate", ""))
    try:
        funding_rate_pct: float | str = float(funding_rate) * 100
    except ValueError:
        funding_rate_pct = ""
    first_open_ms = int(contract.get("firstOpenDate") or 0)
    funding_ms = int(record.get("timepoint") or 0)
    return {
        "symbol": str(contract.get("symbol", "")),
        "base_currency": str(contract.get("baseCurrency", "")),
        "market_type": str(contract.get("marketType", "")),
        "status": str(contract.get("status", "")),
        "first_open_time_utc": timestamp_to_iso(first_open_ms, UTC),
        "first_open_time_beijing": timestamp_to_iso(first_open_ms, BEIJING),
        "funding_time_utc": timestamp_to_iso(funding_ms, UTC),
        "funding_time_beijing": timestamp_to_iso(funding_ms, BEIJING),
        "funding_time_ms": funding_ms,
        "funding_rate": funding_rate,
        "funding_rate_pct": funding_rate_pct,
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
            expected_hours = float(
                row.get("configured_interval_hours") or DEFAULT_FUNDING_INTERVAL_HOURS
            )
            tolerance_hours = INTERVAL_TOLERANCE_MINUTES / 60
            row["elapsed_hours_since_previous"] = round(elapsed_hours, 6)
            row["is_expected_interval"] = abs(elapsed_hours - expected_hours) <= tolerance_hours
            row["is_8h_interval"] = abs(elapsed_hours - 8) <= tolerance_hours
        else:
            row["elapsed_hours_since_previous"] = ""
            row["is_expected_interval"] = ""
            row["is_8h_interval"] = ""
        previous_by_symbol[symbol] = funding_ms
    return merged


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
        window_end_ms = (
            floor_boundary
            if funding_ms - floor_boundary <= tolerance_ms
            else floor_boundary + period_ms
        )
        key = (symbol, window_end_ms)
        group = groups.setdefault(
            key,
            {
                "symbol": symbol,
                "base_currency": row.get("base_currency", ""),
                "window_end_ms": window_end_ms,
                "rate": Decimal("0"),
                "settlement_count": 0,
                "configured_interval_hours": float(
                    row.get("configured_interval_hours") or DEFAULT_FUNDING_INTERVAL_HOURS
                ),
                "first_settlement_ms": funding_ms,
                "last_settlement_ms": funding_ms,
            },
        )
        group["rate"] += rate
        group["settlement_count"] += 1
        group["first_settlement_ms"] = min(group["first_settlement_ms"], funding_ms)
        group["last_settlement_ms"] = max(group["last_settlement_ms"], funding_ms)

    rows: list[dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        interval_hours = group["configured_interval_hours"]
        expected_count = max(1, round(8 / interval_hours))
        rate = group["rate"]
        rows.append(
            {
                "symbol": group["symbol"],
                "base_currency": group["base_currency"],
                "window_end_utc": timestamp_to_iso(group["window_end_ms"], UTC),
                "window_end_beijing": timestamp_to_iso(group["window_end_ms"], BEIJING),
                "window_end_ms": group["window_end_ms"],
                "funding_rate_8h": str(rate),
                "funding_rate_8h_pct": str(rate * Decimal("100")),
                "settlement_count": group["settlement_count"],
                "expected_settlement_count": expected_count,
                "is_complete_by_current_interval": group["settlement_count"] >= expected_count,
                "configured_interval_hours": interval_hours,
                "first_settlement_time_utc": timestamp_to_iso(group["first_settlement_ms"], UTC),
                "last_settlement_time_utc": timestamp_to_iso(group["last_settlement_ms"], UTC),
            }
        )
    return rows


def build_latest_rows(
    contracts: list[dict[str, Any]],
    history: list[dict[str, Any]],
    errors: dict[str, str],
) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in history:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)

    rows: list[dict[str, Any]] = []
    for contract in contracts:
        symbol = str(contract.get("symbol", ""))
        records = by_symbol.get(symbol, [])
        latest = records[-1] if records else {}
        interval_hours, interval_source = configured_interval(contract)
        rows.append(
            {
                "symbol": symbol,
                "base_currency": str(contract.get("baseCurrency", "")),
                "market_type": str(contract.get("marketType", "")),
                "status": str(contract.get("status", "")),
                "first_open_time_beijing": timestamp_to_iso(contract.get("firstOpenDate"), BEIJING),
                "configured_interval_hours": interval_hours,
                "interval_source": interval_source,
                "funding_time_utc": latest.get("funding_time_utc", ""),
                "funding_time_beijing": latest.get("funding_time_beijing", ""),
                "funding_rate": latest.get("funding_rate", ""),
                "funding_rate_pct": latest.get("funding_rate_pct", ""),
                "current_mark_price": contract.get("markPrice", ""),
                "records_total": len(records),
                "error": errors.get(symbol, ""),
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch every KuCoin TradFi perpetual funding settlement record."
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=[],
        help="Optional KuCoin symbols, for example: --symbols MUUSDTM TSLAUSDTM",
    )
    parser.add_argument(
        "--start-time",
        help="UTC ISO time for a lower bound. Defaults to each contract first open time.",
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
        raise RuntimeError("no active KuCoin TradFi futures contracts found")

    end_time = parse_cli_time(args.end_time, end_of_day=True) if args.end_time else dt.datetime.now(UTC)
    end_ms = int(end_time.timestamp() * 1000)
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

    new_rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    print(f"active_tradifi_contracts={len(contracts)}")
    for index, contract in enumerate(contracts, start=1):
        symbol = str(contract.get("symbol", ""))
        first_open_ms = int(contract.get("firstOpenDate") or 0)
        start_ms = int(requested_start.timestamp() * 1000) if requested_start else first_open_ms
        if not args.full_refresh and symbol in last_time_by_symbol:
            start_ms = max(start_ms, last_time_by_symbol[symbol] + 1)

        interval_hours, interval_source = configured_interval(contract)
        try:
            records = fetch_funding_history(symbol, start_ms, end_ms) if start_ms <= end_ms else []
            for record in records:
                new_rows.append(
                    build_history_row(contract, record, interval_hours, interval_source)
                )
            print(f"[{index:03d}/{len(contracts):03d}] {symbol}: new_records={len(records)}")
        except Exception as exc:
            errors[symbol] = str(exc)
            print(f"[{index:03d}/{len(contracts):03d}] {symbol}: error={exc}", file=sys.stderr)
        time.sleep(0.05)

    history = merge_and_annotate(existing + new_rows)
    normalized_8h = normalize_to_8h_windows(history)
    latest = build_latest_rows(contracts, history, errors)
    write_csv(HISTORY_CSV, HISTORY_FIELDS, history)
    write_csv(NORMALIZED_8H_CSV, NORMALIZED_8H_FIELDS, normalized_8h)
    write_csv(LATEST_CSV, LATEST_FIELDS, latest)

    generated_at = dt.datetime.now(UTC)
    interval_counts: dict[str, int] = {}
    for row in latest:
        key = str(row["configured_interval_hours"])
        interval_counts[key] = interval_counts.get(key, 0) + 1
    summary = {
        "generated_at_utc": generated_at.isoformat(),
        "generated_at_beijing": generated_at.astimezone(BEIJING).isoformat(),
        "sources": {
            "contracts": ACTIVE_CONTRACTS_URL,
            "funding_history": FUNDING_HISTORY_URL,
        },
        "filters": "status=Open, marketType!=CRYPTO, quoteCurrency=USDT, settleCurrency=USDT",
        "note": "Positive fundingRate means longs pay shorts. The normalized file sums all actual settlements into fixed 8-hour windows.",
        "active_contract_count": len(contracts),
        "interval_hours_counts": interval_counts,
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
    print(f"wrote {NORMALIZED_8H_CSV.name}")
    print(f"wrote {LATEST_CSV.name}")
    print(f"wrote {SUMMARY_JSON.name}")
    return 1 if errors and len(errors) == len(contracts) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
