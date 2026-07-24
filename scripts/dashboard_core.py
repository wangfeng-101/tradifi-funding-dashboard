from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from turnover import load_turnover_cache


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "sources.json"
WINDOW_ORDER = ("1d", "7d", "30d", "since_common_listing")
WINDOW_DELTAS = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "since_common_listing": None,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(value: str) -> Path:
    return (BASE_DIR / value).resolve()


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_beijing(value: datetime) -> str:
    return value.astimezone(timezone(timedelta(hours=8))).isoformat()


def load_latest_sources(config: dict[str, Any], errors: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for exchange_id, source in config.get("latest_sources", {}).items():
        path = resolve_path(source["path"])
        result[exchange_id] = {}
        try:
            rows = read_csv(path)
        except (OSError, csv.Error) as exc:
            errors.append(f"{exchange_id} latest: {exc}")
            continue

        for row in rows:
            underlying = row.get(source["underlying_column"], "").strip().upper()
            if not underlying:
                continue
            result[exchange_id][underlying] = {
                "symbol": row.get(source["symbol_column"], ""),
                "rate_pct": as_float(row.get(source["rate_pct_column"])),
                "time": row.get(source["time_column"], ""),
                "price": as_float(row.get(source["price_column"])),
                "error": row.get("error", ""),
            }
    return result


def contract_index(path: Path, left: str, right: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in read_csv(path):
        underlying = row.get("underlying", "").strip().upper()
        if not underlying:
            continue
        result[underlying] = {
            "common_start_time": row.get("common_start_time_beijing", ""),
            "later_listing_exchange": row.get("later_listing_exchange", ""),
            "listings": {
                left: row.get(f"{left}_listing_time_beijing", ""),
                right: row.get(f"{right}_listing_time_beijing", ""),
            },
        }
    return result


def load_pair_source(
    source: dict[str, Any],
    latest: dict[str, dict[str, dict[str, Any]]],
    errors: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_id = source["id"]
    left = source["left_exchange"]
    right = source["right_exchange"]
    comparison_path = resolve_path(source["comparison_path"])
    contracts_path = resolve_path(source["contracts_path"])
    metadata_path = resolve_path(source["metadata_path"])

    try:
        rows = read_csv(comparison_path)
        contracts = contract_index(contracts_path, left, right)
        metadata = read_json(metadata_path)
    except (OSError, csv.Error, json.JSONDecodeError) as exc:
        errors.append(f"{source_id}: {exc}")
        return [], {}

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        underlying = row.get("underlying", "").strip().upper()
        window = row.get("window", "")
        if not underlying or window not in WINDOW_ORDER:
            continue

        contract = contracts.get(underlying, {})
        opportunity = grouped.setdefault(
            underlying,
            {
                "id": f"{source_id}:{underlying}",
                "pair_id": source_id,
                "underlying": underlying,
                "exchanges": [left, right],
                "strategy_type": "cross_perp",
                "venues": [
                    {"key": left, "exchange": left, "market": "perp"},
                    {"key": right, "exchange": right, "market": "perp"},
                ],
                "symbols": {
                    left: row.get(f"{left}_symbol", ""),
                    right: row.get(f"{right}_symbol", ""),
                },
                "listings": contract.get("listings", {}),
                "common_start_time": contract.get(
                    "common_start_time", row.get("common_start_time_beijing", "")
                ),
                "later_listing_exchange": contract.get("later_listing_exchange", ""),
                "latest": {
                    left: latest.get(left, {}).get(underlying),
                    right: latest.get(right, {}).get(underlying),
                },
                "windows": {},
            },
        )

        left_rate = as_float(row.get(f"{left}_cumulative_rate_pct"))
        right_rate = as_float(row.get(f"{right}_cumulative_rate_pct"))
        opportunity["windows"][window] = {
            "start_time": row.get("window_start_beijing", ""),
            "end_time": row.get("window_end_beijing", ""),
            "is_full_window": as_bool(row.get("is_full_window")),
            "rates_pct": {left: left_rate, right: right_rate},
            "records": {
                left: as_int(row.get(f"{left}_records")),
                right: as_int(row.get(f"{right}_records")),
            },
            "signed_diff_pct": as_float(row.get(f"{left}_minus_{right}_rate_pct")),
            "gross_diff_pct": as_float(row.get("best_gross_rate_pct")),
            "short_exchange": row.get("short_exchange", ""),
            "long_exchange": row.get("long_exchange", ""),
            "short_leg": {"exchange": row.get("short_exchange", ""), "market": "perp"},
            "long_leg": {"exchange": row.get("long_exchange", ""), "market": "perp"},
        }

    return list(grouped.values()), metadata


def load_market_sources(config: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    markets: dict[str, dict[str, Any]] = {}
    for exchange, source in config.get("market_sources", {}).items():
        try:
            funding_rows = read_csv(resolve_path(source["funding_history_path"]))
        except (OSError, csv.Error) as exc:
            errors.append(f"{exchange} funding history: {exc}")
            continue

        futures: dict[str, dict[str, Any]] = {}
        for row in funding_rows:
            underlying = row.get(source["funding_underlying_column"], "").strip().upper()
            if not underlying:
                continue
            item = futures.setdefault(
                underlying,
                {
                    "symbol": row.get(source["funding_symbol_column"], ""),
                    "listing_time": row.get(source["funding_listing_column"], ""),
                    "interval_hours": as_float(row.get(source.get("funding_interval_column", ""))) or 8,
                    "category": row.get(source.get("funding_category_column", ""), ""),
                    "turnover_24h_usdt": None,
                    "mark_price": None,
                    "records": [],
                },
            )
            try:
                item["records"].append(
                    (
                        parse_datetime(row.get(source["funding_time_column"])),
                        as_float(row.get(source["funding_rate_pct_column"])),
                    )
                )
            except (ValueError, TypeError):
                continue
        for item in futures.values():
            item["records"].sort(key=lambda record: record[0])

        spots: dict[str, dict[str, Any]] = {}
        spot_path = source.get("spot_path")
        if spot_path:
            try:
                for row in read_csv(resolve_path(spot_path)):
                    market_column = source.get("spot_market_column")
                    if market_column and row.get(market_column) != source.get("spot_market_value", "spot"):
                        continue
                    underlying = row.get(source["spot_underlying_column"], "").strip().upper()
                    listing = row.get(source["spot_listing_column"], "")
                    if underlying and listing:
                        spots[underlying] = {
                            "symbol": row.get(source["spot_symbol_column"], ""),
                            "listing_time": listing,
                            "turnover_24h_usdt": as_float(row.get(source.get("spot_turnover_column", ""))) if source.get("spot_turnover_column") else None,
                        }
            except (OSError, csv.Error) as exc:
                errors.append(f"{exchange} spot: {exc}")
        ticker_path = source.get("ticker_path")
        if ticker_path:
            try:
                for row in read_csv(resolve_path(ticker_path)):
                    symbol = row.get(source.get("ticker_symbol_column", "symbol"), "")
                    market = row.get(source.get("ticker_market_column", "market"), "")
                    turnover = as_float(row.get(source.get("ticker_turnover_column", "turnover_24h_usdt")))
                    mark_price = as_float(row.get(source.get("ticker_mark_price_column", "mark_price")))
                    target = futures if market == source.get("ticker_perp_value", "perp") else spots
                    for item in target.values():
                        if item["symbol"] == symbol:
                            item["turnover_24h_usdt"] = turnover
                            if target is futures:
                                item["mark_price"] = mark_price
                            break
            except (OSError, csv.Error) as exc:
                errors.append(f"{exchange} ticker: {exc}")
        markets[exchange] = {"futures": futures, "spots": spots}
    return markets


def funding_data_end(*futures: dict[str, Any]) -> datetime:
    now = datetime.now(timezone.utc)
    candidates = []
    for future in futures:
        if future["records"]:
            interval = timedelta(hours=future.get("interval_hours") or 8)
            candidates.append(future["records"][-1][0] + interval)
    return min([now, *candidates]) if candidates else now


def history_is_complete(
    records: list[tuple[datetime, float]],
    window_start: datetime,
    interval_hours: float,
) -> bool:
    if not records:
        return False
    allowance = timedelta(hours=max(interval_hours, 1)) + timedelta(minutes=5)
    return records[0][0] <= window_start + allowance


def requested_window_is_complete(
    common_start: datetime,
    requested_start: datetime,
    delta: timedelta | None,
) -> bool:
    """A fixed window is complete only when both markets existed for all of it."""
    return delta is None or common_start <= requested_start


def annualize_rate(cumulative_rate_pct: float, start: datetime, end: datetime) -> float:
    elapsed_days = (end - start).total_seconds() / 86_400
    if elapsed_days <= 0:
        return 0.0
    return cumulative_rate_pct / elapsed_days * 365


def build_spot_perp_opportunity(
    strategy_type: str,
    spot_exchange: str,
    perp_exchange: str,
    underlying: str,
    spot: dict[str, Any],
    future: dict[str, Any],
    latest: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    spot_key = f"{spot_exchange}_spot"
    perp_key = f"{perp_exchange}_perp"
    common_start = max(parse_datetime(spot["listing_time"]), parse_datetime(future["listing_time"]))
    calculation_end = funding_data_end(future)
    windows: dict[str, Any] = {}
    for window, delta in WINDOW_DELTAS.items():
        requested_start = common_start if delta is None else calculation_end - delta
        window_start = max(common_start, requested_start)
        records = [rate for timepoint, rate in future["records"] if window_start <= timepoint <= calculation_end]
        cumulative = sum(records)
        elapsed_days = (calculation_end - window_start).total_seconds() / 86_400
        annualized = annualize_rate(cumulative, window_start, calculation_end)
        if cumulative >= 0:
            short_leg = {"exchange": perp_exchange, "market": "perp"}
            long_leg = {"exchange": spot_exchange, "market": "spot"}
        else:
            short_leg = {"exchange": spot_exchange, "market": "spot"}
            long_leg = {"exchange": perp_exchange, "market": "perp"}
        windows[window] = {
            "start_time": iso_beijing(window_start),
            "end_time": iso_beijing(calculation_end),
            "is_full_window": requested_window_is_complete(
                common_start, requested_start, delta
            ) and history_is_complete(
                future["records"], window_start, future.get("interval_hours") or 8
            ),
            "rates_pct": {spot_key: 0.0, perp_key: cumulative},
            "annualized_rates_pct": {spot_key: 0.0, perp_key: annualized},
            "records": {spot_key: 0, perp_key: len(records)},
            "elapsed_days": elapsed_days,
            "signed_diff_pct": -cumulative,
            "gross_diff_pct": abs(cumulative),
            "annualized_signed_diff_pct": -annualized,
            "annualized_gross_diff_pct": abs(annualized),
            "short_exchange": short_leg["exchange"],
            "long_exchange": long_leg["exchange"],
            "short_leg": short_leg,
            "long_leg": long_leg,
        }

    return {
        "id": f"{strategy_type}:{spot_exchange}:{perp_exchange}:{underlying}",
        "pair_id": f"{spot_exchange}-{perp_exchange}",
        "strategy_type": strategy_type,
        "underlying": underlying,
        "exchanges": list(dict.fromkeys([spot_exchange, perp_exchange])),
        "venues": [
            {"key": spot_key, "exchange": spot_exchange, "market": "spot"},
            {"key": perp_key, "exchange": perp_exchange, "market": "perp"},
        ],
        "symbols": {spot_key: spot["symbol"], perp_key: future["symbol"]},
        "listings": {spot_key: spot["listing_time"], perp_key: future["listing_time"]},
        "common_start_time": iso_beijing(common_start),
        "later_listing_exchange": "",
        "latest": {spot_key: None, perp_key: latest.get(perp_exchange, {}).get(underlying)},
        "turnover_24h_usdt": {
            spot_key: spot.get("turnover_24h_usdt"),
            perp_key: future.get("turnover_24h_usdt"),
        },
        "windows": windows,
    }


def build_spot_perp_opportunities(
    markets: dict[str, dict[str, Any]],
    latest: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    opportunities: list[dict[str, Any]] = []
    for spot_exchange, spot_market in markets.items():
        for perp_exchange, perp_market in markets.items():
            strategy = "same_spot_perp" if spot_exchange == perp_exchange else "cross_spot_perp"
            funded_futures = {
                underlying
                for underlying, future in perp_market["futures"].items()
                if future["records"]
            }
            common = set(spot_market["spots"]) & funded_futures
            for underlying in sorted(common):
                opportunities.append(
                    build_spot_perp_opportunity(
                        strategy,
                        spot_exchange,
                        perp_exchange,
                        underlying,
                        spot_market["spots"][underlying],
                        perp_market["futures"][underlying],
                        latest,
                    )
                )
    return opportunities


def build_cross_perp_opportunity(
    left_exchange: str,
    right_exchange: str,
    underlying: str,
    left: dict[str, Any],
    right: dict[str, Any],
    latest: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    common_start = max(parse_datetime(left["listing_time"]), parse_datetime(right["listing_time"]))
    calculation_end = funding_data_end(left, right)
    windows: dict[str, Any] = {}
    for window, delta in WINDOW_DELTAS.items():
        requested_start = common_start if delta is None else calculation_end - delta
        window_start = max(common_start, requested_start)
        left_records = [rate for timepoint, rate in left["records"] if window_start <= timepoint <= calculation_end]
        right_records = [rate for timepoint, rate in right["records"] if window_start <= timepoint <= calculation_end]
        left_sum = sum(left_records)
        right_sum = sum(right_records)
        difference = left_sum - right_sum
        elapsed_days = (calculation_end - window_start).total_seconds() / 86_400
        left_annualized = annualize_rate(left_sum, window_start, calculation_end)
        right_annualized = annualize_rate(right_sum, window_start, calculation_end)
        annualized_difference = left_annualized - right_annualized
        if difference >= 0:
            short_leg = {"exchange": left_exchange, "market": "perp"}
            long_leg = {"exchange": right_exchange, "market": "perp"}
        else:
            short_leg = {"exchange": right_exchange, "market": "perp"}
            long_leg = {"exchange": left_exchange, "market": "perp"}
        windows[window] = {
            "start_time": iso_beijing(window_start),
            "end_time": iso_beijing(calculation_end),
            "is_full_window": requested_window_is_complete(
                common_start, requested_start, delta
            ) and history_is_complete(
                left["records"], window_start, left.get("interval_hours") or 8
            ) and history_is_complete(
                right["records"], window_start, right.get("interval_hours") or 8
            ),
            "rates_pct": {left_exchange: left_sum, right_exchange: right_sum},
            "annualized_rates_pct": {
                left_exchange: left_annualized,
                right_exchange: right_annualized,
            },
            "records": {left_exchange: len(left_records), right_exchange: len(right_records)},
            "elapsed_days": elapsed_days,
            "signed_diff_pct": difference,
            "gross_diff_pct": abs(difference),
            "annualized_signed_diff_pct": annualized_difference,
            "annualized_gross_diff_pct": abs(annualized_difference),
            "short_exchange": short_leg["exchange"],
            "long_exchange": long_leg["exchange"],
            "short_leg": short_leg,
            "long_leg": long_leg,
        }
    return {
        "id": f"cross_perp:{left_exchange}:{right_exchange}:{underlying}",
        "pair_id": f"{left_exchange}-{right_exchange}",
        "strategy_type": "cross_perp",
        "underlying": underlying,
        "exchanges": [left_exchange, right_exchange],
        "venues": [
            {"key": left_exchange, "exchange": left_exchange, "market": "perp"},
            {"key": right_exchange, "exchange": right_exchange, "market": "perp"},
        ],
        "symbols": {left_exchange: left["symbol"], right_exchange: right["symbol"]},
        "listings": {left_exchange: left["listing_time"], right_exchange: right["listing_time"]},
        "common_start_time": iso_beijing(common_start),
        "later_listing_exchange": left_exchange if parse_datetime(left["listing_time"]) >= parse_datetime(right["listing_time"]) else right_exchange,
        "latest": {
            left_exchange: latest.get(left_exchange, {}).get(underlying),
            right_exchange: latest.get(right_exchange, {}).get(underlying),
        },
        "turnover_24h_usdt": {
            left_exchange: left.get("turnover_24h_usdt"),
            right_exchange: right.get("turnover_24h_usdt"),
        },
        "windows": windows,
    }


def build_cross_perp_opportunities(
    markets: dict[str, dict[str, Any]],
    latest: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    opportunities: list[dict[str, Any]] = []
    exchanges = list(markets)
    for left_index, left_exchange in enumerate(exchanges):
        for right_exchange in exchanges[left_index + 1:]:
            left_market = markets[left_exchange]["futures"]
            right_market = markets[right_exchange]["futures"]
            left_funded = {underlying for underlying, future in left_market.items() if future["records"]}
            right_funded = {underlying for underlying, future in right_market.items() if future["records"]}
            for underlying in sorted(left_funded & right_funded):
                opportunities.append(
                    build_cross_perp_opportunity(
                        left_exchange,
                        right_exchange,
                        underlying,
                        left_market[underlying],
                        right_market[underlying],
                        latest,
                    )
                )
    return opportunities


def build_payload() -> dict[str, Any]:
    config = read_json(CONFIG_PATH)
    errors: list[str] = []
    latest = load_latest_sources(config, errors)
    opportunities: list[dict[str, Any]] = []
    metadata_by_pair: dict[str, Any] = {}

    for source in config.get("comparison_sources", []):
        try:
            metadata = read_json(resolve_path(source["metadata_path"]))
            metadata_by_pair[source["id"]] = {
                "generated_at_utc": metadata.get("generated_at_utc", ""),
                "calculation_end_time_utc": metadata.get("calculation_end_time_utc", ""),
            }
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{source['id']} metadata: {exc}")

    markets = load_market_sources(config, errors)
    for exchange, exchange_markets in markets.items():
        exchange_latest = latest.setdefault(exchange, {})
        for underlying, future in exchange_markets["futures"].items():
            if underlying in exchange_latest or not future["records"]:
                continue
            timestamp, rate_pct = future["records"][-1]
            exchange_latest[underlying] = {
                "symbol": future["symbol"],
                "rate_pct": rate_pct,
                "time": iso_beijing(timestamp),
                "price": future.get("mark_price") or 0,
                "error": "",
            }

    opportunities.extend(build_spot_perp_opportunities(markets, latest))
    opportunities.extend(build_cross_perp_opportunities(markets, latest))
    turnover = load_turnover_cache()
    turnover_venues = turnover.get("venues", {})
    for opportunity in opportunities:
        opportunity.setdefault("turnover_24h_usdt", {})
        for venue in opportunity["venues"]:
            market_key = f"{venue['exchange']}_{venue['market']}"
            symbol = opportunity["symbols"].get(venue["key"], "")
            cached = turnover_venues.get(market_key, {}).get(symbol)
            if cached is not None:
                opportunity["turnover_24h_usdt"][venue["key"]] = cached
    opportunities.sort(key=lambda item: (item["strategy_type"], item["underlying"], item["pair_id"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "windows": list(WINDOW_ORDER),
        "window_labels": {
            "1d": "最近 1 天",
            "7d": "最近 7 天",
            "30d": "最近 30 天",
            "since_common_listing": "共同上线至今",
        },
        "strategy_labels": {
            "same_spot_perp": "同所 现货-合约",
            "cross_spot_perp": "跨所 现货-合约",
            "cross_perp": "跨所 合约-合约",
        },
        "exchanges": config.get("exchanges", {}),
        "metadata_by_pair": metadata_by_pair,
        "turnover_metadata": {
            "generated_at_utc": turnover.get("generated_at_utc", ""),
            "errors": turnover.get("errors", []),
        },
        "opportunities": opportunities,
        "errors": errors,
    }
