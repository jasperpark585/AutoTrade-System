from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


class KakaoNotifier:
    def __init__(self, token: str | None = None):
        self.token = token

    def send(self, message: str) -> bool:
        if not self.token:
            logger.info("Kakao token missing; message skipped.")
            return False
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = {"template_object": '{"object_type":"text","text":"' + message + '","link":{"web_url":"https://example.com"}}'}
        try:
            resp = requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send", headers=headers, data=payload, timeout=5)
            ok = resp.status_code == 200
            if not ok:
                logger.error("Kakao notify failed: %s", resp.text)
            return ok
        except Exception as exc:
            logger.error("Kakao notify error: %s", exc)
            return False
