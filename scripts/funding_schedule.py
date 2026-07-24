from __future__ import annotations


def settlement_is_due(
    latest_timestamp_ms: int,
    interval_hours: float,
    end_timestamp_ms: int,
) -> bool:
    if latest_timestamp_ms <= 0 or interval_hours <= 0:
        return True
    interval_ms = int(interval_hours * 3_600_000)
    return end_timestamp_ms >= latest_timestamp_ms + interval_ms
