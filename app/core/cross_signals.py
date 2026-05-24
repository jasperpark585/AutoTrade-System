from __future__ import annotations

from typing import Any


GOLDEN_CROSS = "GOLDEN_CROSS"
DEAD_CROSS = "DEAD_CROSS"
MIDLONG_GOLDEN_CROSS = "MIDLONG_GOLDEN_CROSS"
MIDLONG_DEAD_CROSS = "MIDLONG_DEAD_CROSS"


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


def _senkou_span2(highs: list[float], lows: list[float], window: int = 52) -> float | None:
    if window <= 0 or len(highs) < window or len(lows) < window:
        return None
    return (max(highs[-window:]) + min(lows[-window:])) / 2


def enrich_bars_with_cross_signals(
    bars: list[dict[str, Any]],
    short_window: int = 5,
    long_window: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    short_window = max(2, int(short_window or 5))
    long_window = max(short_window + 1, int(long_window or 20))
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    enriched: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    prev_short: float | None = None
    prev_long: float | None = None
    prev_ma20: float | None = None
    prev_span2: float | None = None

    for row in bars:
        close = _to_float(row.get("close"))
        high = _to_float(row.get("high")) or close
        low = _to_float(row.get("low")) or close
        out = dict(row)
        if close is None:
            out["ma_short"] = None
            out["ma_long"] = None
            out["ma20"] = None
            out["senkou_span2"] = None
            out["cross_signal"] = ""
            enriched.append(out)
            continue

        closes.append(close)
        highs.append(float(high or close))
        lows.append(float(low or close))
        ma_short = _sma(closes, short_window)
        ma_long = _sma(closes, long_window)
        ma20 = _sma(closes, 20)
        span2 = _senkou_span2(highs, lows, 52)
        signal = ""
        if prev_short is not None and prev_long is not None and ma_short is not None and ma_long is not None:
            if prev_short <= prev_long and ma_short > ma_long:
                signal = GOLDEN_CROSS
            elif prev_short >= prev_long and ma_short < ma_long:
                signal = DEAD_CROSS
        if prev_ma20 is not None and prev_span2 is not None and ma20 is not None and span2 is not None:
            if prev_ma20 <= prev_span2 and ma20 > span2:
                signal = MIDLONG_GOLDEN_CROSS
            elif prev_ma20 >= prev_span2 and ma20 < span2:
                signal = MIDLONG_DEAD_CROSS

        out["ma_short"] = round(ma_short, 4) if ma_short is not None else None
        out["ma_long"] = round(ma_long, 4) if ma_long is not None else None
        out["ma20"] = round(ma20, 4) if ma20 is not None else None
        out["senkou_span2"] = round(span2, 4) if span2 is not None else None
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
                    "ma20": out["ma20"],
                    "senkou_span2": out["senkou_span2"],
                    "support_low": out.get("low"),
                    "resistance_high": out.get("high"),
                }
            )

        if ma_short is not None and ma_long is not None:
            prev_short = ma_short
            prev_long = ma_long
        if ma20 is not None and span2 is not None:
            prev_ma20 = ma20
            prev_span2 = span2

    return enriched, signals
