from __future__ import annotations

import pandas as pd

from app.core.database import Database


def load_closed_trades(db: Database) -> pd.DataFrame:
    df = db.fetch_df("SELECT * FROM trades WHERE status='CLOSED'")
    if df.empty:
        return df
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["holding_minutes"] = (df["exit_time"] - df["entry_time"]).dt.total_seconds() / 60
    return df


def aggregate_performance(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    if period == "D":
        key = df["exit_time"].dt.strftime("%Y-%m-%d")
    elif period == "M":
        key = df["exit_time"].dt.strftime("%Y-%m")
    elif period == "Q":
        key = df["exit_time"].dt.to_period("Q").astype(str)
    elif period == "Y":
        key = df["exit_time"].dt.strftime("%Y")
    else:
        raise ValueError("period must be one of D/M/Q/Y")

    grouped = df.groupby(key)
    out = grouped.agg(
        total_profit=("pnl", lambda x: x[x > 0].sum()),
        total_loss=("pnl", lambda x: x[x < 0].sum()),
        net_pnl=("pnl", "sum"),
        wins=("pnl", lambda x: (x > 0).sum()),
        trades=("id", "count"),
        avg_holding_minutes=("holding_minutes", "mean"),
    ).reset_index(names=["period"])

    out["win_rate_pct"] = (out["wins"] / out["trades"] * 100).round(2)
    out["profit_factor"] = (out["total_profit"] / out["total_loss"].abs().replace(0, 1)).round(2)
    out["mdd_estimate"] = _estimate_mdd(df)
    return out


def symbol_contribution(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return (
        df.groupby("symbol", as_index=False)
        .agg(net_pnl=("pnl", "sum"), trades=("id", "count"), win_rate=("pnl", lambda x: (x > 0).mean() * 100))
        .sort_values("net_pnl", ascending=False)
    )


def _estimate_mdd(df: pd.DataFrame) -> float:
    curve = df.sort_values("exit_time")["pnl"].cumsum()
    peak = curve.cummax()
    drawdown = curve - peak
    return float(drawdown.min()) if not drawdown.empty else 0.0
