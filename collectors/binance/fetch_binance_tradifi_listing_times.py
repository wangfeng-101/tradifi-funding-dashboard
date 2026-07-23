from __future__ import annotations

import csv
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SPOT_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
SPOT_KLINES_URL = "https://api.binance.com/api/v3/klines"

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
SPOT_SYMBOLS_CSV = OUTPUT_DIR / "binance_tradifi_spot_symbols.csv"
LISTING_TIMES_CSV = OUTPUT_DIR / "binance_tradifi_spot_margin_listing_times.csv"
LISTING_TIMES_JSON = OUTPUT_DIR / "binance_tradifi_spot_margin_listing_times.json"


def fetch_json(url: str, params: dict[str, Any] | None = None) -> Any:
    full_url = url
    if params:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        full_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def ms_to_utc(value: Any) -> str:
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp_ms <= 0:
        return ""
    return dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt.timezone.utc).isoformat()


def ms_to_beijing(value: Any) -> str:
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp_ms <= 0:
        return ""
    tz = dt.timezone(dt.timedelta(hours=8))
    return dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=tz).isoformat()


def read_spot_symbols(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"missing {path}; run fetch_binance_tradifi_symbols.py first")
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def read_existing_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def first_spot_kline(symbol: str) -> list[Any] | None:
    data = fetch_json(
        SPOT_KLINES_URL,
        {
            "symbol": symbol,
            "interval": "1m",
            "startTime": 0,
            "limit": 1,
        },
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


def build_rows(
    symbol_rows: list[dict[str, str]],
    existing_rows: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    existing_by_symbol = {
        row.get("symbol", ""): row
        for row in (existing_rows or [])
        if row.get("symbol")
    }
    rows: list[dict[str, Any]] = []

    for index, symbol_row in enumerate(symbol_rows, start=1):
        symbol = symbol_row["symbol"]
        existing = existing_by_symbol.get(symbol, {})
        requested_kline = False
        row: dict[str, Any] = {
            "symbol": symbol,
            "base_asset": symbol_row.get("base_asset", ""),
            "underlying": symbol_row.get("underlying", ""),
            "quote_asset": symbol_row.get("quote_asset", ""),
            "status": symbol_row.get("status", ""),
            "is_spot_trading_allowed": symbol_row.get("is_spot_trading_allowed", ""),
            "is_margin_trading_allowed": symbol_row.get("is_margin_trading_allowed", ""),
            "spot_first_kline_time_utc": existing.get("spot_first_kline_time_utc", ""),
            "spot_first_kline_time_beijing": existing.get(
                "spot_first_kline_time_beijing", ""
            ),
            "spot_first_open": existing.get("spot_first_open", ""),
            "spot_first_close": existing.get("spot_first_close", ""),
            "margin_listing_time_utc": "",
            "margin_listing_time_beijing": "",
            "margin_time_source": "not_exposed_by_public_spot_exchangeInfo; current margin status only",
            "note": existing.get("note", ""),
        }

        if not row["spot_first_kline_time_utc"]:
            requested_kline = True
            try:
                kline = first_spot_kline(symbol)
                if kline:
                    row["spot_first_kline_time_utc"] = ms_to_utc(kline[0])
                    row["spot_first_kline_time_beijing"] = ms_to_beijing(kline[0])
                    row["spot_first_open"] = kline[1]
                    row["spot_first_close"] = kline[4]
                    row["note"] = ""
                else:
                    row["note"] = "no kline returned"
            except Exception as exc:
                row["note"] = f"kline_error: {exc}"

        rows.append(row)

        if requested_kline and index < len(symbol_rows):
            time.sleep(0.05)

    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0].keys()) if rows else ["symbol"]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    symbol_rows = read_spot_symbols(SPOT_SYMBOLS_CSV)
    existing_rows = read_existing_rows(LISTING_TIMES_CSV)
    rows = build_rows(symbol_rows, existing_rows)

    write_csv(LISTING_TIMES_CSV, rows)

    summary = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources": {
            "spot_symbols": str(SPOT_SYMBOLS_CSV),
            "spot_klines": SPOT_KLINES_URL,
        },
        "spot_listing_time_method": "earliest 1m spot kline open time",
        "margin_listing_time_method": "not available from public spot exchangeInfo; output includes current isMarginTradingAllowed only",
        "count": len(rows),
        "rows": rows,
    }
    LISTING_TIMES_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"count={len(rows)}")
    print(f"wrote {LISTING_TIMES_CSV.name}")
    print(f"wrote {LISTING_TIMES_JSON.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
