from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
REFERENCE_DIR = BASE_DIR.parent
UTC = dt.timezone.utc
BEIJING = dt.timezone(dt.timedelta(hours=8))
TRADFI_CATEGORIES = {"stocks", "metals", "indices", "forex", "commodities"}
EXCHANGES = ("okx", "gate", "bitget", "bybit", "phemex")
LISTING_TIME_CACHE: dict[tuple[str, str, str], dt.datetime] = {}

MARKET_FIELDS = [
    "exchange", "market", "symbol", "underlying", "raw_base", "quote", "status",
    "category", "listing_time_utc", "listing_time_beijing", "funding_interval_hours",
    "turnover_24h_usdt", "last_price", "mark_price", "bid_price", "ask_price",
    "source_updated_at_utc",
]
FUNDING_FIELDS = [
    "exchange", "symbol", "underlying", "category", "listing_time_utc",
    "listing_time_beijing", "funding_interval_hours", "funding_time_utc",
    "funding_time_beijing", "funding_rate", "funding_rate_pct", "error",
]


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def from_milliseconds(value: Any) -> dt.datetime | None:
    parsed = integer(value)
    if not parsed or parsed <= 0:
        return None
    return dt.datetime.fromtimestamp(parsed / 1000, tz=UTC)


def from_seconds(value: Any) -> dt.datetime | None:
    parsed = integer(value)
    if not parsed or parsed <= 0:
        return None
    return dt.datetime.fromtimestamp(parsed, tz=UTC)


def iso(value: dt.datetime | None, timezone: dt.tzinfo = UTC) -> str:
    return value.astimezone(timezone).isoformat() if value else ""


def cached_listing_time(
    exchange: str,
    market: str,
    symbol: str,
) -> dt.datetime | None:
    return LISTING_TIME_CACHE.get((exchange, market, symbol))


def read_reference_underlyings() -> set[str]:
    references: set[str] = set()
    paths = [
        (REFERENCE_DIR / "binance" / "outputs" / "binance_tradifi_futures_symbols.csv", "base_asset"),
        (REFERENCE_DIR / "kucoin" / "outputs" / "kucoin_tradifi_futures_contracts.csv", "base_currency"),
    ]
    for path, column in paths:
        if not path.exists():
            continue
        with path.open("r", newline="", encoding="utf-8-sig") as file:
            for row in csv.DictReader(file):
                value = row.get(column, "").strip().upper()
                if value:
                    references.add(value)
    return references


REFERENCE_UNDERLYINGS = read_reference_underlyings()


def normalize_xstock(base: str) -> str:
    value = base.strip().upper()
    if value in REFERENCE_UNDERLYINGS:
        return value
    if value.endswith("X") and value[:-1] in REFERENCE_UNDERLYINGS:
        return value[:-1]
    return value


def normalize_bitget_spot(base: str) -> str:
    value = base.strip()
    if len(value) > 1 and value[0] == "r":
        value = value[1:]
    return value.upper()


def normalize_bybit_xstock(base: str) -> str:
    value = base.strip().upper()
    return value[:-1] if value.endswith("X") else value


def http_json(
    url: str,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    body: Any = None,
) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed for {url}: {last_error}")


class RateLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval = interval_seconds
        self.next_time = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_time - now)
            self.next_time = max(now, self.next_time) + self.interval
        if delay:
            time.sleep(delay)


LIMITERS = {
    "okx": RateLimiter(0.22),
    "gate": RateLimiter(0.04),
    "bitget": RateLimiter(0.06),
    "bybit": RateLimiter(0.04),
    "phemex": RateLimiter(0.06),
}


def market_row(
    exchange: str,
    market: str,
    symbol: str,
    underlying: str,
    raw_base: str,
    quote: str,
    status: str,
    category: str,
    listing: dt.datetime | None,
    interval_hours: float | None,
    ticker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticker = ticker or {}
    return {
        "exchange": exchange,
        "market": market,
        "symbol": symbol,
        "underlying": underlying,
        "raw_base": raw_base,
        "quote": quote,
        "status": status,
        "category": category,
        "listing_time_utc": iso(listing),
        "listing_time_beijing": iso(listing, BEIJING),
        "funding_interval_hours": interval_hours or "",
        "turnover_24h_usdt": ticker.get("turnover"),
        "last_price": ticker.get("last"),
        "mark_price": ticker.get("mark"),
        "bid_price": ticker.get("bid"),
        "ask_price": ticker.get("ask"),
        "source_updated_at_utc": iso(dt.datetime.now(UTC)),
    }


def discover_okx() -> list[dict[str, Any]]:
    instruments = http_json("https://www.okx.com/api/v5/public/instruments", {"instType": "SWAP"})
    tickers = http_json("https://www.okx.com/api/v5/market/tickers", {"instType": "SWAP"})
    spot_instruments = http_json("https://www.okx.com/api/v5/public/instruments", {"instType": "SPOT"})
    spot_tickers = http_json("https://www.okx.com/api/v5/market/tickers", {"instType": "SPOT"})
    ticker_by_symbol: dict[str, dict[str, Any]] = {}
    for item in tickers.get("data", []):
        last = number(item.get("last"))
        base_volume = number(item.get("volCcy24h"))
        ticker_by_symbol[item.get("instId", "")] = {
            "last": last,
            "mark": None,
            "bid": number(item.get("bidPx")),
            "ask": number(item.get("askPx")),
            "turnover": last * base_volume if last is not None and base_volume is not None else None,
        }
    rows = []
    for item in instruments.get("data", []):
        if item.get("state") != "live" or item.get("settleCcy") != "USDT":
            continue
        if str(item.get("groupId")) not in {"6", "7"}:
            continue
        symbol = item.get("instId", "")
        base = symbol.split("-")[0].upper()
        rows.append(
            market_row(
                "okx", "perp", symbol, base, base, "USDT", item.get("state", ""),
                f"rwa_group_{item.get('groupId')}", from_milliseconds(item.get("listTime")), 8,
                ticker_by_symbol.get(symbol),
            )
        )

    spot_ticker_by_symbol = {
        item.get("instId", ""): {
            "last": number(item.get("last")),
            "mark": None,
            "bid": number(item.get("bidPx")),
            "ask": number(item.get("askPx")),
            "turnover": number(item.get("volCcy24h")),
        }
        for item in spot_tickers.get("data", [])
    }
    for item in spot_instruments.get("data", []):
        if (
            item.get("state") != "live"
            or item.get("quoteCcy") != "USDT"
            or str(item.get("instCategory", "")) != "3"
        ):
            continue
        symbol = item.get("instId", "")
        raw_base = str(item.get("baseCcy", "")).upper()
        underlying = raw_base[1:] if raw_base.startswith("X") else raw_base
        rows.append(
            market_row(
                "okx", "spot", symbol, underlying, raw_base, "USDT", item.get("state", ""),
                "stock", from_milliseconds(item.get("listTime")), None,
                spot_ticker_by_symbol.get(symbol),
            )
        )
    return rows


def gate_spot_listing(symbol: str) -> dt.datetime | None:
    cached = cached_listing_time("gate", "spot", symbol)
    if cached:
        return cached
    try:
        LIMITERS["gate"].wait()
        candles = http_json(
            "https://api.gateio.ws/api/v4/spot/candlesticks",
            {"currency_pair": symbol, "interval": "1d", "limit": 1000},
        )
        times = [integer(row[0]) for row in candles if isinstance(row, list) and row]
        valid = [value for value in times if value]
        return from_seconds(min(valid)) if valid else None
    except Exception:
        return None


def discover_gate() -> list[dict[str, Any]]:
    contracts = http_json("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    futures_tickers = http_json("https://api.gateio.ws/api/v4/futures/usdt/tickers")
    spot_pairs = http_json("https://api.gateio.ws/api/v4/spot/currency_pairs")
    spot_tickers = http_json("https://api.gateio.ws/api/v4/spot/tickers")
    futures_ticker_by_symbol = {
        item.get("contract", ""): {
            "turnover": number(item.get("volume_24h_quote")),
            "last": number(item.get("last")),
            "mark": number(item.get("mark_price")),
            "bid": number(item.get("highest_bid")),
            "ask": number(item.get("lowest_ask")),
        }
        for item in futures_tickers
    }
    spot_ticker_by_symbol = {
        item.get("currency_pair", ""): {
            "turnover": number(item.get("quote_volume")),
            "last": number(item.get("last")),
            "mark": None,
            "bid": number(item.get("highest_bid")),
            "ask": number(item.get("lowest_ask")),
        }
        for item in spot_tickers
    }
    rows: list[dict[str, Any]] = []
    tradfi_by_raw_base: dict[str, dict[str, Any]] = {}
    for item in contracts:
        category = str(item.get("contract_type", "")).lower()
        if item.get("status") != "trading" or category not in TRADFI_CATEGORIES:
            continue
        symbol = item.get("name", "")
        raw_base = symbol.removesuffix("_USDT").upper()
        if raw_base == "USDC":
            continue
        underlying = normalize_xstock(raw_base)
        listing = from_seconds(item.get("launch_time"))
        tradfi_by_raw_base[raw_base] = {"underlying": underlying, "listing": listing, "category": category}
        rows.append(
            market_row(
                "gate", "perp", symbol, underlying, raw_base, "USDT", item.get("status", ""),
                category, listing, (number(item.get("funding_interval")) or 28800) / 3600,
                futures_ticker_by_symbol.get(symbol),
            )
        )

    for item in spot_pairs:
        raw_base = str(item.get("base", "")).upper()
        if (
            item.get("quote") != "USDT"
            or item.get("trade_status") != "tradable"
            or raw_base not in tradfi_by_raw_base
        ):
            continue
        metadata = tradfi_by_raw_base[raw_base]
        symbol = item.get("id", "")
        listing = gate_spot_listing(symbol) or metadata["listing"]
        rows.append(
            market_row(
                "gate", "spot", symbol, metadata["underlying"], raw_base, "USDT",
                item.get("trade_status", ""), metadata["category"], listing, None,
                spot_ticker_by_symbol.get(symbol),
            )
        )
    return rows


def discover_bitget() -> list[dict[str, Any]]:
    futures = http_json("https://api.bitget.com/api/v3/market/instruments", {"category": "USDT-FUTURES"})
    spots = http_json("https://api.bitget.com/api/v3/market/instruments", {"category": "SPOT"})
    futures_tickers = http_json("https://api.bitget.com/api/v3/market/tickers", {"category": "USDT-FUTURES"})
    spot_tickers = http_json("https://api.bitget.com/api/v3/market/tickers", {"category": "SPOT"})
    ticker_maps = {}
    for market, payload in (("perp", futures_tickers), ("spot", spot_tickers)):
        ticker_maps[market] = {
            item.get("symbol", ""): {
                "turnover": number(item.get("turnover24h")),
                "last": number(item.get("lastPrice")),
                "mark": number(item.get("markPrice")),
                "bid": number(item.get("bid1Price")),
                "ask": number(item.get("ask1Price")),
            }
            for item in payload.get("data", [])
        }

    rows: list[dict[str, Any]] = []
    for item in futures.get("data", []):
        category = str(item.get("symbolType", "")).lower()
        if (
            item.get("status") != "online"
            or item.get("quoteCoin") != "USDT"
            or str(item.get("isRwa", "")).upper() != "YES"
            or category not in {"stock", "metal", "commodity"}
        ):
            continue
        symbol = item.get("symbol", "")
        base = str(item.get("baseCoin", "")).upper()
        rows.append(
            market_row(
                "bitget", "perp", symbol, base, base, "USDT", item.get("status", ""), category,
                from_milliseconds(item.get("launchTime")), number(item.get("fundInterval")) or 8,
                ticker_maps["perp"].get(symbol),
            )
        )

    for item in spots.get("data", []):
        if (
            item.get("status") != "online"
            or item.get("quoteCoin") != "USDT"
            or str(item.get("symbolType", "")).lower() != "stock"
        ):
            continue
        symbol = item.get("symbol", "")
        raw_base = str(item.get("baseCoin", ""))
        rows.append(
            market_row(
                "bitget", "spot", symbol, normalize_bitget_spot(raw_base), raw_base, "USDT",
                item.get("status", ""), "stock", from_milliseconds(item.get("launchTime")), None,
                ticker_maps["spot"].get(symbol),
            )
        )
    return rows


def bybit_instruments(category: str) -> list[dict[str, Any]]:
    if category == "spot":
        return http_json("https://api.bybit.com/v5/market/instruments-info", {"category": "spot"}).get("result", {}).get("list", [])
    rows: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {"category": category, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = http_json("https://api.bybit.com/v5/market/instruments-info", params)
        result = payload.get("result", {})
        rows.extend(result.get("list", []))
        cursor = result.get("nextPageCursor", "")
        if not cursor:
            break
    return rows


def bybit_spot_listing_time(symbol: str) -> dt.datetime | None:
    cached = cached_listing_time("bybit", "spot", symbol)
    if cached:
        return cached
    try:
        payload = http_json(
            "https://api.bybit.com/v5/market/kline",
            {"category": "spot", "symbol": symbol, "interval": "D", "limit": 1000},
        )
        timestamps = [
            integer(row[0])
            for row in payload.get("result", {}).get("list", [])
            if isinstance(row, list) and row
        ]
        valid_timestamps = [value for value in timestamps if value and value > 0]
        return from_milliseconds(min(valid_timestamps)) if valid_timestamps else None
    except Exception:
        return None


def discover_bybit() -> list[dict[str, Any]]:
    spots = bybit_instruments("spot")
    futures = bybit_instruments("linear")
    spot_tickers = http_json("https://api.bybit.com/v5/market/tickers", {"category": "spot"})
    futures_tickers = http_json("https://api.bybit.com/v5/market/tickers", {"category": "linear"})
    spot_ticker_by_symbol = {
        item.get("symbol", ""): {
            "turnover": number(item.get("turnover24h")),
            "last": number(item.get("lastPrice")),
            "bid": number(item.get("bid1Price")),
            "ask": number(item.get("ask1Price")),
        }
        for item in spot_tickers.get("result", {}).get("list", [])
    }
    futures_ticker_by_symbol = {
        item.get("symbol", ""): {
            "turnover": number(item.get("turnover24h")),
            "last": number(item.get("lastPrice")),
            "mark": number(item.get("markPrice")),
            "bid": number(item.get("bid1Price")),
            "ask": number(item.get("ask1Price")),
        }
        for item in futures_tickers.get("result", {}).get("list", [])
    }

    rows: list[dict[str, Any]] = []
    for item in spots:
        category = str(item.get("symbolType", "")).lower()
        if (
            item.get("status") != "Trading"
            or item.get("quoteCoin") != "USDT"
            or category != "xstocks"
        ):
            continue
        symbol = item.get("symbol", "")
        raw_base = str(item.get("baseCoin", "")).upper()
        listing = from_milliseconds(item.get("launchTime")) or bybit_spot_listing_time(symbol)
        rows.append(
            market_row(
                "bybit", "spot", symbol, normalize_bybit_xstock(raw_base), raw_base, "USDT",
                item.get("status", ""), "stock", listing, None,
                spot_ticker_by_symbol.get(symbol),
            )
        )

    for item in futures:
        category = str(item.get("symbolType", "")).lower()
        if (
            item.get("status") != "Trading"
            or item.get("quoteCoin") != "USDT"
            or item.get("contractType") != "LinearPerpetual"
            or category not in {"stock", "commodity"}
        ):
            continue
        symbol = item.get("symbol", "")
        base = str(item.get("baseCoin", "")).upper()
        rows.append(
            market_row(
                "bybit", "perp", symbol, base, base, "USDT", item.get("status", ""), category,
                from_milliseconds(item.get("launchTime")), (number(item.get("fundingInterval")) or 480) / 60,
                futures_ticker_by_symbol.get(symbol),
            )
        )
    return rows


def discover_phemex() -> list[dict[str, Any]]:
    products = http_json("https://api.phemex.com/public/products")
    tickers = http_json("https://api.phemex.com/md/v2/ticker/24hr/all")
    ticker_by_symbol = {
        item.get("symbol", ""): {
            "turnover": number(item.get("turnoverRv")),
            "last": number(item.get("closeRp")),
            "mark": number(item.get("markPriceRp")),
            "bid": None,
            "ask": None,
        }
        for item in tickers.get("result", [])
    }
    rows = []
    for item in products.get("data", {}).get("perpProductsV2", []):
        if (
            item.get("status") != "Listed"
            or item.get("quoteCurrency") != "USDT"
            or item.get("perpProductSubType") != "TradFi"
        ):
            continue
        symbol = item.get("symbol", "")
        base = str(item.get("baseCurrency", "")).upper()
        rows.append(
            market_row(
                "phemex", "perp", symbol, base, base, "USDT", item.get("status", ""),
                str(item.get("perpProductSubGroup") or "tradfi"), from_milliseconds(item.get("listTime")),
                (number(item.get("fundingInterval")) or 28800) / 3600, ticker_by_symbol.get(symbol),
            )
        )
    return rows


DISCOVERERS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "okx": discover_okx,
    "gate": discover_gate,
    "bitget": discover_bitget,
    "bybit": discover_bybit,
    "phemex": discover_phemex,
}


def funding_okx(market: dict[str, Any], cutoff: dt.datetime) -> list[tuple[dt.datetime, float]]:
    LIMITERS["okx"].wait()
    payload = http_json(
        "https://www.okx.com/api/v5/public/funding-rate-history",
        {"instId": market["symbol"], "limit": 400},
    )
    result = []
    for item in payload.get("data", []):
        timestamp = from_milliseconds(item.get("fundingTime"))
        rate = number(item.get("realizedRate") or item.get("fundingRate"))
        if timestamp and rate is not None and timestamp >= cutoff:
            result.append((timestamp, rate))
    return result


def funding_gate(market: dict[str, Any], cutoff: dt.datetime) -> list[tuple[dt.datetime, float]]:
    LIMITERS["gate"].wait()
    payload = http_json(
        "https://api.gateio.ws/api/v4/futures/usdt/funding_rate",
        {"contract": market["symbol"], "limit": 1000, "from": int(cutoff.timestamp())},
    )
    result = []
    for item in payload:
        timestamp = from_seconds(item.get("t"))
        rate = number(item.get("r"))
        if timestamp and rate is not None and timestamp >= cutoff:
            result.append((timestamp, rate))
    return result


def funding_bitget(market: dict[str, Any], cutoff: dt.datetime) -> list[tuple[dt.datetime, float]]:
    result: list[tuple[dt.datetime, float]] = []
    for page in range(1, 6):
        LIMITERS["bitget"].wait()
        payload = http_json(
            "https://api.bitget.com/api/v3/market/history-fund-rate",
            {"category": "USDT-FUTURES", "symbol": market["symbol"], "limit": 100, "cursor": page},
        )
        rows = payload.get("data", {}).get("resultList", [])
        if not rows:
            break
        oldest: dt.datetime | None = None
        for item in rows:
            timestamp = from_milliseconds(item.get("fundingRateTimestamp"))
            rate = number(item.get("fundingRate"))
            if timestamp:
                oldest = timestamp if oldest is None else min(oldest, timestamp)
            if timestamp and rate is not None and timestamp >= cutoff:
                result.append((timestamp, rate))
        if len(rows) < 100 or (oldest and oldest <= cutoff):
            break
    return result


def funding_bybit(market: dict[str, Any], cutoff: dt.datetime) -> list[tuple[dt.datetime, float]]:
    result: list[tuple[dt.datetime, float]] = []
    end_time: int | None = None
    for _ in range(4):
        LIMITERS["bybit"].wait()
        params: dict[str, Any] = {"category": "linear", "symbol": market["symbol"], "limit": 200}
        if end_time:
            params["endTime"] = end_time
        payload = http_json("https://api.bybit.com/v5/market/funding/history", params)
        rows = payload.get("result", {}).get("list", [])
        if not rows:
            break
        times: list[dt.datetime] = []
        for item in rows:
            timestamp = from_milliseconds(item.get("fundingRateTimestamp"))
            rate = number(item.get("fundingRate"))
            if timestamp:
                times.append(timestamp)
            if timestamp and rate is not None and timestamp >= cutoff:
                result.append((timestamp, rate))
        oldest = min(times) if times else None
        if len(rows) < 200 or not oldest or oldest <= cutoff:
            break
        end_time = int(oldest.timestamp() * 1000) - 1
    return result


def funding_phemex(market: dict[str, Any], cutoff: dt.datetime) -> list[tuple[dt.datetime, float]]:
    result: list[tuple[dt.datetime, float]] = []
    start = int(cutoff.timestamp() * 1000)
    end = int(dt.datetime.now(UTC).timestamp() * 1000)
    for _ in range(8):
        LIMITERS["phemex"].wait()
        payload = http_json(
            "https://api.phemex.com/api-data/public/data/funding-rate-history",
            {"symbol": f".{market['symbol']}FR8H", "start": start, "end": end, "limit": 100},
        )
        rows = payload.get("data", {}).get("rows", [])
        if not rows:
            break
        times: list[dt.datetime] = []
        for item in rows:
            timestamp = from_milliseconds(item.get("fundingTime"))
            rate = number(item.get("fundingRate"))
            if timestamp:
                times.append(timestamp)
            if timestamp and rate is not None and timestamp >= cutoff:
                result.append((timestamp, rate))
        newest = max(times) if times else None
        if len(rows) < 100 or not newest or int(newest.timestamp() * 1000) >= end:
            break
        next_start = int(newest.timestamp() * 1000) + 1
        if next_start <= start:
            break
        start = next_start
    return result


FUNDING_FETCHERS = {
    "okx": funding_okx,
    "gate": funding_gate,
    "bitget": funding_bitget,
    "bybit": funding_bybit,
    "phemex": funding_phemex,
}


def build_funding_rows(
    market: dict[str, Any],
    records: list[tuple[dt.datetime, float]],
    error: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for timestamp, rate in sorted(set(records), key=lambda item: item[0]):
        rows.append(
            {
                "exchange": market["exchange"],
                "symbol": market["symbol"],
                "underlying": market["underlying"],
                "category": market["category"],
                "listing_time_utc": market["listing_time_utc"],
                "listing_time_beijing": market["listing_time_beijing"],
                "funding_interval_hours": market["funding_interval_hours"],
                "funding_time_utc": iso(timestamp),
                "funding_time_beijing": iso(timestamp, BEIJING),
                "funding_rate": rate,
                "funding_rate_pct": rate * 100,
                "error": "",
            }
        )
    if not rows and error:
        rows.append(
            {
                "exchange": market["exchange"], "symbol": market["symbol"],
                "underlying": market["underlying"], "category": market["category"],
                "listing_time_utc": market["listing_time_utc"],
                "listing_time_beijing": market["listing_time_beijing"],
                "funding_interval_hours": market["funding_interval_hours"],
                "funding_time_utc": "", "funding_time_beijing": "", "funding_rate": "",
                "funding_rate_pct": "", "error": error,
            }
        )
    return rows


def funding_rows_for_market(
    market: dict[str, Any],
    cutoff: dt.datetime,
) -> tuple[list[dict[str, Any]], str]:
    try:
        records = FUNDING_FETCHERS[market["exchange"]](market, cutoff)
        error = ""
    except Exception as exc:
        records = []
        error = str(exc)

    return (
        build_funding_rows(
            market,
            records,
            error,
        ),
        error,
    )


def parse_iso_time(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def cache_listing_times(rows: list[dict[str, str]]) -> None:
    for row in rows:
        exchange = str(row.get("exchange", ""))
        market = str(row.get("market", ""))
        symbol = str(row.get("symbol", ""))
        listing = parse_iso_time(row.get("listing_time_utc"))
        if exchange and market and symbol and listing:
            LISTING_TIME_CACHE[(exchange, market, symbol)] = listing


def restore_cached_listing_times(markets: list[dict[str, Any]]) -> None:
    for market in markets:
        if market.get("listing_time_utc"):
            continue
        listing = cached_listing_time(
            str(market.get("exchange", "")),
            str(market.get("market", "")),
            str(market.get("symbol", "")),
        )
        if listing:
            market["listing_time_utc"] = iso(listing)
            market["listing_time_beijing"] = iso(listing, BEIJING)


def funding_cutoff(
    market: dict[str, Any],
    history_cutoff: dt.datetime,
    latest_existing: dt.datetime | None,
) -> dt.datetime:
    cutoff = history_cutoff
    listing = parse_iso_time(market.get("listing_time_utc"))
    if listing:
        cutoff = max(cutoff, listing)
    if latest_existing:
        cutoff = max(cutoff, latest_existing + dt.timedelta(milliseconds=1))
    return cutoff


def retained_funding_rows(
    rows: list[dict[str, str]],
    active_symbols: set[str],
    history_cutoff: dt.datetime,
) -> tuple[list[dict[str, str]], dict[str, dt.datetime]]:
    retained: list[dict[str, str]] = []
    latest_by_symbol: dict[str, dt.datetime] = {}
    for row in rows:
        symbol = str(row.get("symbol", ""))
        timestamp = parse_iso_time(row.get("funding_time_utc"))
        if symbol not in active_symbols or timestamp is None or timestamp < history_cutoff:
            continue
        retained.append(row)
        latest_by_symbol[symbol] = max(
            timestamp,
            latest_by_symbol.get(symbol, timestamp),
        )
    return retained, latest_by_symbol


def merge_funding_rows(
    existing: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for row in existing + new_rows:
        symbol = str(row.get("symbol", ""))
        funding_time = str(row.get("funding_time_utc", ""))
        if funding_time:
            records[(symbol, funding_time)] = row
        elif row.get("error"):
            diagnostics[symbol] = row
    merged = list(records.values()) + list(diagnostics.values())
    return sorted(
        merged,
        key=lambda row: (
            str(row.get("underlying", "")),
            str(row.get("symbol", "")),
            str(row.get("funding_time_utc", "")),
        ),
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def run_exchange(exchange: str, history_days: int, workers: int) -> dict[str, Any]:
    print(f"[{exchange}] discovering TradFi markets", flush=True)
    markets_path = OUTPUT_DIR / f"{exchange}_tradifi_markets.csv"
    cache_listing_times(read_csv(markets_path))
    markets = DISCOVERERS[exchange]()
    restore_cached_listing_times(markets)
    markets.sort(key=lambda row: (row["market"], row["underlying"], row["symbol"]))
    futures = [row for row in markets if row["market"] == "perp"]
    spots = [row for row in markets if row["market"] == "spot"]
    write_csv(markets_path, markets, MARKET_FIELDS)
    print(f"[{exchange}] spot={len(spots)} perp={len(futures)}; fetching {history_days}d funding", flush=True)

    history_cutoff = dt.datetime.now(UTC) - dt.timedelta(days=history_days)
    funding_path = OUTPUT_DIR / f"{exchange}_tradifi_funding.csv"
    existing_rows, latest_by_symbol = retained_funding_rows(
        read_csv(funding_path),
        {str(market["symbol"]) for market in futures},
        history_cutoff,
    )
    new_funding_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        tasks = {
            executor.submit(
                funding_rows_for_market,
                market,
                funding_cutoff(
                    market,
                    history_cutoff,
                    latest_by_symbol.get(str(market["symbol"])),
                ),
            ): market
            for market in futures
        }
        for future in as_completed(tasks):
            market = tasks[future]
            try:
                rows, error = future.result()
                new_funding_rows.extend(rows)
                if error:
                    errors.append(f"{market['symbol']}: {error}")
            except Exception as exc:
                errors.append(f"{market['symbol']}: {exc}")
            completed += 1
            if completed % 25 == 0 or completed == len(futures):
                print(f"[{exchange}] funding {completed}/{len(futures)} errors={len(errors)}", flush=True)

    funding_rows = merge_funding_rows(existing_rows, new_funding_rows)
    write_csv(funding_path, funding_rows, FUNDING_FIELDS)
    summary = {
        "exchange": exchange,
        "generated_at_utc": iso(dt.datetime.now(UTC)),
        "history_days_requested": history_days,
        "spot_count": len(spots),
        "perp_count": len(futures),
        "funding_record_count": sum(1 for row in funding_rows if row["funding_time_utc"]),
        "retained_funding_record_count": len(existing_rows),
        "new_funding_record_count": sum(
            1 for row in new_funding_rows if row.get("funding_time_utc")
        ),
        "error_count": len(errors),
        "errors": errors,
    }
    (OUTPUT_DIR / f"{exchange}_tradifi_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch normalized TradFi markets and funding data")
    parser.add_argument("--exchange", choices=["all", *EXCHANGES], default="all")
    parser.add_argument("--history-days", type=int, default=35)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    selected = EXCHANGES if args.exchange == "all" else (args.exchange,)
    summaries = []
    if len(selected) == 1:
        exchange = selected[0]
        try:
            summaries.append(run_exchange(exchange, args.history_days, args.workers))
        except Exception as exc:
            print(f"[{exchange}] FAILED: {exc}", flush=True)
            summaries.append({"exchange": exchange, "fatal_error": str(exc)})
    else:
        with ThreadPoolExecutor(max_workers=len(selected)) as executor:
            tasks = {
                executor.submit(
                    run_exchange,
                    exchange,
                    args.history_days,
                    args.workers,
                ): exchange
                for exchange in selected
            }
            for future in as_completed(tasks):
                exchange = tasks[future]
                try:
                    summaries.append(future.result())
                except Exception as exc:
                    print(f"[{exchange}] FAILED: {exc}", flush=True)
                    summaries.append({"exchange": exchange, "fatal_error": str(exc)})
    summaries.sort(key=lambda item: str(item.get("exchange", "")))
    print(json.dumps(summaries, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
