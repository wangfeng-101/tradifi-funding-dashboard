from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / "turnover_24h.json"

SOURCES = {
    "binance_spot": {
        "url": "https://api.binance.com/api/v3/ticker/24hr",
        "kind": "binance",
    },
    "binance_perp": {
        "url": "https://fapi.binance.com/fapi/v1/ticker/24hr",
        "kind": "binance",
    },
    "kucoin_perp": {
        "url": "https://api-futures.kucoin.com/api/v1/contracts/active",
        "kind": "kucoin",
    },
    "okx_perp": {
        "url": "https://www.okx.com/api/v5/market/tickers?instType=SWAP",
        "kind": "okx",
    },
    "okx_spot": {
        "url": "https://www.okx.com/api/v5/market/tickers?instType=SPOT",
        "kind": "okx_spot",
    },
    "gate_spot": {
        "url": "https://api.gateio.ws/api/v4/spot/tickers",
        "kind": "gate_spot",
    },
    "gate_perp": {
        "url": "https://api.gateio.ws/api/v4/futures/usdt/tickers",
        "kind": "gate_perp",
    },
    "bitget_spot": {
        "url": "https://api.bitget.com/api/v3/market/tickers?category=SPOT",
        "kind": "bitget",
    },
    "bitget_perp": {
        "url": "https://api.bitget.com/api/v3/market/tickers?category=USDT-FUTURES",
        "kind": "bitget",
    },
    "bybit_perp": {
        "url": "https://api.bybit.com/v5/market/tickers?category=linear",
        "kind": "bybit",
    },
    "bybit_spot": {
        "url": "https://api.bybit.com/v5/market/tickers?category=spot",
        "kind": "bybit",
    },
    "phemex_perp": {
        "url": "https://api.phemex.com/md/v2/ticker/24hr/all",
        "kind": "phemex",
    },
}


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
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


def refresh_turnover_cache() -> dict[str, Any]:
    venues: dict[str, dict[str, float]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        futures = {
            executor.submit(fetch_json, source["url"]): (venue, source)
            for venue, source in SOURCES.items()
        }
        for future in as_completed(futures):
            venue, source = futures[future]
            try:
                venues[venue] = normalize(source, future.result())
            except Exception as exc:  # Individual exchanges must not block the others.
                errors.append(f"{venue}: {exc}")

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
