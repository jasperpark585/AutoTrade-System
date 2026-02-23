from __future__ import annotations

from typing import Any

from app.services.kis_client import KISError

try:
    from tenacity import RetryError
except ModuleNotFoundError:  # pragma: no cover
    class RetryError(Exception):
        pass


def unwrap_exception(exc: Exception) -> tuple[str, str, dict[str, Any]]:
    err_type = type(exc).__name__
    message = str(exc)
    detail: dict[str, Any] = {}

    root_exc: Exception = exc
    if isinstance(exc, RetryError):
        last_exc = None
        try:
            last_exc = exc.last_attempt.exception()
        except Exception:
            last_exc = None
        if last_exc:
            root_exc = last_exc
            err_type = f"RetryError -> {type(last_exc).__name__}"
            message = str(last_exc)

    if isinstance(root_exc, KISError):
        detail = root_exc.detail or {}
        summary = " ".join(
            [
                f"status={detail.get('http_status')}" if detail.get("http_status") is not None else "",
                f"rt_cd={detail.get('rt_cd')}" if detail.get("rt_cd") else "",
                f"msg1={detail.get('msg1')}" if detail.get("msg1") else "",
            ]
        ).strip()
        if summary:
            message = f"{message} | {summary}"

    return err_type, message, detail
