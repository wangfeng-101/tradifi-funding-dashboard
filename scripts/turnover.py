from __future__ import annotations

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


try:
    from scripts.http_client import JsonHttpClient, JsonRequestError
except ModuleNotFoundError:
    from http_client import JsonHttpClient, JsonRequestError


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
CACHE_PATH = BASE_DIR / "turnover_24h.json"
HTTP_CLIENT = JsonHttpClient(timeout=30)

REMOTE_SOURCES = {
    "binance_spot": {
        "url": "https://api.binance.com/api/v3/ticker/24hr",
        "kind": "binance",
    },
    "binance_perp": {
        "url": "https://fapi.binance.com/fapi/v1/ticker/24hr",
        "kind": "binance",
    },
}

MULTI_OUTPUT_DIR = ROOT_DIR / "collectors" / "multi_exchange" / "outputs"
LOCAL_MARKET_SOURCES = {
    "okx_perp": (MULTI_OUTPUT_DIR / "okx_tradifi_markets.csv", "perp"),
    "okx_spot": (MULTI_OUTPUT_DIR / "okx_tradifi_markets.csv", "spot"),
    "gate_perp": (MULTI_OUTPUT_DIR / "gate_tradifi_markets.csv", "perp"),
    "gate_spot": (MULTI_OUTPUT_DIR / "gate_tradifi_markets.csv", "spot"),
    "bitget_perp": (MULTI_OUTPUT_DIR / "bitget_tradifi_markets.csv", "perp"),
    "bitget_spot": (MULTI_OUTPUT_DIR / "bitget_tradifi_markets.csv", "spot"),
    "bybit_perp": (MULTI_OUTPUT_DIR / "bybit_tradifi_markets.csv", "perp"),
    "bybit_spot": (MULTI_OUTPUT_DIR / "bybit_tradifi_markets.csv", "spot"),
    "phemex_perp": (MULTI_OUTPUT_DIR / "phemex_tradifi_markets.csv", "perp"),
}
KUCOIN_LATEST_PATH = (
    ROOT_DIR
    / "collectors"
    / "kucoin"
    / "outputs"
    / "kucoin_tradifi_funding_8h_latest.csv"
)
BINANCE_SPOT_SYMBOLS_PATH = (
    ROOT_DIR
    / "collectors"
    / "binance"
    / "outputs"
    / "binance_tradifi_spot_symbols.csv"
)


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_json(url: str, params: dict[str, Any] | None = None) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return HTTP_CLIENT.request_json(url, params)
        except JsonRequestError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"request failed: {last_error}")


def normalize(source: dict[str, str], payload: Any) -> dict[str, float]:
    if source["kind"] == "binance":
        if not isinstance(payload, list):
            raise ValueError("unexpected Binance ticker response")
        return {
            str(item.get("symbol", "")): value
            for item in payload
            if (value := number(item.get("quoteVolume"))) is not None
        }

    kind = source["kind"]
    if kind == "kucoin":
        if not isinstance(payload, dict) or payload.get("code") != "200000":
            raise ValueError("unexpected KuCoin contracts response")
        return {
            str(item.get("symbol", "")): value
            for item in payload.get("data", [])
            if (value := number(item.get("turnoverOf24h"))) is not None
        }
    if kind == "okx":
        if str(payload.get("code")) != "0":
            raise ValueError("unexpected OKX ticker response")
        result = {}
        for item in payload.get("data", []):
            last = number(item.get("last"))
            base_volume = number(item.get("volCcy24h"))
            if last is not None and base_volume is not None:
                result[str(item.get("instId", ""))] = last * base_volume
        return result
    if kind == "okx_spot":
        if str(payload.get("code")) != "0":
            raise ValueError("unexpected OKX spot ticker response")
        return {
            str(item.get("instId", "")): value
            for item in payload.get("data", [])
            if (value := number(item.get("volCcy24h"))) is not None
        }
    if kind == "gate_spot":
        return {
            str(item.get("currency_pair", "")): value
            for item in payload
            if (value := number(item.get("quote_volume"))) is not None
        }
    if kind == "gate_perp":
        return {
            str(item.get("contract", "")): value
            for item in payload
            if (value := number(item.get("volume_24h_quote"))) is not None
        }
    if kind == "bitget":
        if payload.get("code") != "00000":
            raise ValueError("unexpected Bitget ticker response")
        return {
            str(item.get("symbol", "")): value
            for item in payload.get("data", [])
            if (value := number(item.get("turnover24h"))) is not None
        }
    if kind == "bybit":
        if payload.get("retCode") != 0:
            raise ValueError("unexpected Bybit ticker response")
        return {
            str(item.get("symbol", "")): value
            for item in payload.get("result", {}).get("list", [])
            if (value := number(item.get("turnover24h"))) is not None
        }
    if kind == "phemex":
        if payload.get("error") is not None:
            raise ValueError("unexpected Phemex ticker response")
        return {
            str(item.get("symbol", "")): value
            for item in payload.get("result", [])
            if (value := number(item.get("turnoverRv"))) is not None
        }
    raise ValueError(f"unknown ticker source: {kind}")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def normalize_csv_turnover(
    rows: list[dict[str, str]],
    *,
    market: str | None = None,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        if market is not None and row.get("market") != market:
            continue
        symbol = str(row.get("symbol", ""))
        value = number(row.get("turnover_24h_usdt"))
        if symbol and value is not None:
            result[symbol] = value
    return result


def load_local_turnover() -> tuple[dict[str, dict[str, float]], list[str]]:
    venues: dict[str, dict[str, float]] = {}
    errors: list[str] = []
    row_cache: dict[Path, list[dict[str, str]]] = {}

    for venue, (path, market) in LOCAL_MARKET_SOURCES.items():
        try:
            if path not in row_cache:
                row_cache[path] = read_csv(path)
            rows = row_cache[path]
            values = normalize_csv_turnover(rows, market=market)
            if not values:
                raise ValueError("no turnover records")
            venues[venue] = values
        except (OSError, ValueError) as exc:
            errors.append(f"{venue}: local turnover: {exc}")

    try:
        values = normalize_csv_turnover(read_csv(KUCOIN_LATEST_PATH))
        if not values:
            raise ValueError("no turnover records")
        venues["kucoin_perp"] = values
    except (OSError, ValueError) as exc:
        errors.append(f"kucoin_perp: local turnover: {exc}")

    return venues, errors


def binance_spot_params() -> dict[str, str]:
    symbols = [
        str(row.get("symbol", ""))
        for row in read_csv(BINANCE_SPOT_SYMBOLS_PATH)
        if row.get("symbol")
    ]
    if not symbols:
        raise ValueError("no Binance spot symbols")
    return {"symbols": json.dumps(symbols, separators=(",", ":"))}


def refresh_turnover_cache() -> dict[str, Any]:
    previous = load_turnover_cache()
    previous_venues = previous.get("venues", {})
    venues, errors = load_local_turnover()
    remote_params: dict[str, dict[str, str] | None] = {
        venue: None for venue in REMOTE_SOURCES
    }
    try:
        remote_params["binance_spot"] = binance_spot_params()
    except (OSError, ValueError) as exc:
        errors.append(f"binance_spot: symbol filter: {exc}")

    with ThreadPoolExecutor(max_workers=len(REMOTE_SOURCES)) as executor:
        futures = {
            executor.submit(
                fetch_json,
                source["url"],
                remote_params[venue],
            ): (venue, source)
            for venue, source in REMOTE_SOURCES.items()
        }
        for future in as_completed(futures):
            venue, source = futures[future]
            try:
                venues[venue] = normalize(source, future.result())
            except Exception as exc:  # Individual exchanges must not block the others.
                errors.append(f"{venue}: {exc}")
                previous_values = previous_venues.get(venue)
                if isinstance(previous_values, dict) and previous_values:
                    venues[venue] = previous_values

    for venue in LOCAL_MARKET_SOURCES.keys() | {"kucoin_perp"}:
        if venue in venues:
            continue
        previous_values = previous_venues.get(venue)
        if isinstance(previous_values, dict) and previous_values:
            venues[venue] = previous_values

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "venues": venues,
        "errors": errors,
    }
    temporary = CACHE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(CACHE_PATH)
    return payload


def load_turnover_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {"generated_at_utc": "", "venues": {}, "errors": ["24h turnover cache not generated"]}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"generated_at_utc": "", "venues": {}, "errors": [f"24h turnover cache: {exc}"]}


if __name__ == "__main__":
    result = refresh_turnover_cache()
    print(
        json.dumps(
            {
                "generated_at_utc": result["generated_at_utc"],
                "venue_counts": {key: len(value) for key, value in result["venues"].items()},
                "errors": result["errors"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
