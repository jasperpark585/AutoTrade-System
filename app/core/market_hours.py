from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

try:
    import holidays
except ModuleNotFoundError:  # pragma: no cover
    holidays = None

KST = ZoneInfo("Asia/Seoul")


@dataclass
class MarketStatus:
    is_open: bool
    can_place_order: bool
    reason: str


def get_market_status(now: datetime | None = None) -> MarketStatus:
    now = now.astimezone(KST) if now else datetime.now(KST)
    is_holiday = False
    if holidays is not None:
        kr_holidays = holidays.country_holidays("KR", years=[now.year])
        is_holiday = now.date() in kr_holidays

    if is_holiday or now.weekday() >= 5:
        return MarketStatus(False, False, "휴장일 또는 주말")

    market_open = time(9, 0)
    market_close = time(15, 30)
    if market_open <= now.time() <= market_close:
        return MarketStatus(True, True, "정규장")
    return MarketStatus(False, False, "장마감 또는 장전")
