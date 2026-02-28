from __future__ import annotations

import json
import logging

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)


class KakaoNotifier:
    def __init__(self, token: str | None = None):
        self.token = token

    def send(self, message: str) -> bool:
        if not self.token:
            logger.info("Kakao token missing; message skipped.")
            return False
        if requests is None:
            logger.warning("requests package missing; Kakao notify skipped.")
            return False
        headers = {"Authorization": f"Bearer {self.token}"}
        template_object = {
            "object_type": "text",
            "text": str(message or ""),
            "link": {"web_url": "https://example.com", "mobile_web_url": "https://example.com"},
        }
        payload = {"template_object": json.dumps(template_object, ensure_ascii=False)}
        try:
            resp = requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send", headers=headers, data=payload, timeout=5)
            ok = resp.status_code == 200
            if not ok:
                logger.error("Kakao notify failed: %s", resp.text)
            return ok
        except Exception as exc:
            logger.error("Kakao notify error: %s", exc)
            return False
