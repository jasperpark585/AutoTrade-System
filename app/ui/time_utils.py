from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def format_retry_time_kst(utc_iso: str | None) -> tuple[str, str]:
    if not utc_iso:
        return "-", "-"
    parsed = datetime.fromisoformat(str(utc_iso).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc_dt = parsed.astimezone(timezone.utc)
    kst_dt = utc_dt.astimezone(ZoneInfo("Asia/Seoul"))
    return kst_dt.strftime("%Y-%m-%d %H:%M:%S %Z"), utc_dt.isoformat()
