from __future__ import annotations

from typing import Any


GOLDEN_CROSS = "GOLDEN_CROSS"
DEAD_CROSS = "DEAD_CROSS"


def _to_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _sma(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def enrich_bars_with_cross_signals(
    bars: list[dict[str, Any]],
    short_window: int = 5,
    long_window: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    short_window = max(2, int(short_window or 5))
    long_window = max(short_window + 1, int(long_window or 20))
    closes: list[float] = []
    enriched: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    prev_short: float | None = None
    prev_long: float | None = None

    for row in bars:
        close = _to_float(row.get("close"))
        out = dict(row)
        if close is None:
            out["ma_short"] = None
            out["ma_long"] = None
            out["cross_signal"] = ""
            enriched.append(out)
            continue

        closes.append(close)
        ma_short = _sma(closes, short_window)
        ma_long = _sma(closes, long_window)
        signal = ""
        if prev_short is not None and prev_long is not None and ma_short is not None and ma_long is not None:
            if prev_short <= prev_long and ma_short > ma_long:
                signal = GOLDEN_CROSS
            elif prev_short >= prev_long and ma_short < ma_long:
                signal = DEAD_CROSS

        out["ma_short"] = round(ma_short, 4) if ma_short is not None else None
        out["ma_long"] = round(ma_long, 4) if ma_long is not None else None
        out["cross_signal"] = signal
        enriched.append(out)

        if signal:
            signals.append(
                {
                    "ts": out.get("ts"),
                    "price": close,
                    "signal": signal,
                    "ma_short": out["ma_short"],
                    "ma_long": out["ma_long"],
                }
            )

        if ma_short is not None and ma_long is not None:
            prev_short = ma_short
            prev_long = ma_long

    return enriched, signals
