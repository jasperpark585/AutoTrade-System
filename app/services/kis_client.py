from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


logger = logging.getLogger(__name__)


class KISError(RuntimeError):
    pass


@dataclass
class Quote:
    symbol: str
    price: float
    volume_ratio: float
    volatility_pct: float
    execution_strength: float
    spread_pct: float
    trend_slope: float


class KISClient:
    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    @retry(
        retry=retry_if_exception_type(KISError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
    )
    def fetch_universe_quotes(self) -> list[Quote]:
        try:
            symbols = ["005930", "000660", "035420", "251270", "068270", "207940"]
            quotes: list[Quote] = []
            for s in symbols:
                base = random.uniform(15000, 120000)
                quotes.append(
                    Quote(
                        symbol=s,
                        price=base,
                        volume_ratio=random.uniform(0.8, 3.8),
                        volatility_pct=random.uniform(0.5, 4.5),
                        execution_strength=random.uniform(80, 140),
                        spread_pct=random.uniform(0.1, 1.5),
                        trend_slope=random.uniform(-0.4, 0.8),
                    )
                )
            return quotes
        except Exception as exc:
            raise KISError(f"KIS quote fetch failed: {exc}") from exc

    @retry(
        retry=retry_if_exception_type(KISError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
    )
    def place_order(self, symbol: str, qty: int, side: str, price: float) -> dict:
        if self.dry_run:
            logger.info("DRY-RUN order simulated: %s %s x%s @ %s", side, symbol, qty, price)
            return {"status": "SIMULATED", "symbol": symbol, "qty": qty, "side": side, "price": price}
        # LIVE mode placeholder - replace with real KIS REST call/signing
        if random.random() < 0.03:
            raise KISError("Order API temporary failure")
        return {"status": "FILLED", "symbol": symbol, "qty": qty, "side": side, "price": price}
