from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

import holidays
import pytz


KST = pytz.timezone("Asia/Seoul")


@dataclass
class MarketStatus:
    is_open: bool
    can_place_order: bool
    reason: str


def get_market_status(now: datetime | None = None) -> MarketStatus:
    now = now.astimezone(KST) if now else datetime.now(KST)
    kr_holidays = holidays.country_holidays("KR", years=[now.year])
    if now.date() in kr_holidays or now.weekday() >= 5:
        return MarketStatus(False, False, "휴장일 또는 주말")

    market_open = time(9, 0)
    market_close = time(15, 30)
    if market_open <= now.time() <= market_close:
        return MarketStatus(True, True, "정규장")
    return MarketStatus(False, False, "장마감 또는 장전")
