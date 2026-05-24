from pathlib import Path
import threading
import time
from urllib.request import urlopen

from kakao import KakaoNotifier


def test_kakao_notifier_reads_token_from_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KAKAO_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("YTA_KAKAO_ACCESS_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "KAKAO_ACCESS_TOKEN=test-token",
                "KAKAO_REST_API_KEY=rest-key",
                "KAKAO_REDIRECT_URI=http://localhost:8501",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    notifier = KakaoNotifier(project_root=tmp_path)

    assert notifier.token == "test-token"
    assert notifier.authorization_url().startswith("https://kauth.kakao.com/oauth/authorize?")


def test_kakao_notifier_refreshes_access_token_on_invalid_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN", "KAKAO_REST_API_KEY", "YTA_KAKAO_ACCESS_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "KAKAO_ACCESS_TOKEN=expired-token",
                "KAKAO_REFRESH_TOKEN=refresh-token",
                "KAKAO_REST_API_KEY=rest-key",
            ]
        ),
        encoding="utf-8",
    )

    class Response:
        def __init__(self, status_code: int, text: str, json_payload: dict | None = None) -> None:
            self.status_code = status_code
            self.text = text
            self._json_payload = json_payload or {}

        def json(self) -> dict:
            return dict(self._json_payload)

    calls: list[str] = []

    def fake_post(url: str, headers=None, data=None, timeout=5):  # type: ignore[no-untyped-def]
        calls.append(url)
        if "memo/default/send" in url and len(calls) == 1:
            return Response(401, '{"msg":"this access token does not exist","code":-401}')
        if "oauth/token" in url:
            assert data["refresh_token"] == "refresh-token"
            return Response(200, "ok", {"access_token": "new-access-token", "refresh_token": "new-refresh-token"})
        return Response(200, "ok")

    monkeypatch.setattr("kakao.requests.post", fake_post)

    notifier = KakaoNotifier(project_root=tmp_path)

    assert notifier.send("테스트", web_url="https://example.com") is True
    assert notifier.token == "new-access-token"
    assert notifier.refresh_token == "new-refresh-token"


def test_kakao_notifier_exchanges_authorization_code_and_updates_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN", "KAKAO_REST_API_KEY", "KAKAO_REDIRECT_URI", "KAKAO_CLIENT_SECRET", "YTA_KAKAO_ACCESS_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "KAKAO_REST_API_KEY=rest-key",
                "KAKAO_REDIRECT_URI=http://localhost:8501",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class Response:
        status_code = 200
        text = "ok"

        def json(self) -> dict:
            return {"access_token": "new-access-token", "refresh_token": "new-refresh-token"}

    def fake_post(url: str, data=None, timeout=10):  # type: ignore[no-untyped-def]
        assert data["grant_type"] == "authorization_code"
        assert data["code"] == "auth-code"
        return Response()

    monkeypatch.setattr("kakao.requests.post", fake_post)

    notifier = KakaoNotifier(project_root=tmp_path)
    payload = notifier.exchange_authorization_code("auth-code")

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert payload["refresh_token"] == "new-refresh-token"
    assert "KAKAO_ACCESS_TOKEN=new-access-token" in env_text
    assert "KAKAO_REFRESH_TOKEN=new-refresh-token" in env_text


def test_kakao_notifier_retries_exchange_without_client_secret_on_koe010(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN", "KAKAO_REST_API_KEY", "KAKAO_REDIRECT_URI", "KAKAO_CLIENT_SECRET", "YTA_KAKAO_ACCESS_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "KAKAO_REST_API_KEY=rest-key",
                "KAKAO_REDIRECT_URI=http://localhost:8501",
                "KAKAO_CLIENT_SECRET=wrong-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class Response:
        def __init__(self, status_code: int, text: str, payload: dict | None = None) -> None:
            self.status_code = status_code
            self.text = text
            self._payload = payload or {}

        def json(self) -> dict:
            return dict(self._payload)

    seen_payloads: list[dict] = []

    def fake_post(url: str, data=None, timeout=10):  # type: ignore[no-untyped-def]
        seen_payloads.append(dict(data))
        if len(seen_payloads) == 1:
            return Response(401, '{"error":"invalid_client","error_description":"Bad client credentials","error_code":"KOE010"}')
        return Response(200, "ok", {"access_token": "new-access-token", "refresh_token": "new-refresh-token"})

    monkeypatch.setattr("kakao.requests.post", fake_post)

    notifier = KakaoNotifier(project_root=tmp_path)
    payload = notifier.exchange_authorization_code("auth-code")

    assert payload["access_token"] == "new-access-token"
    assert "client_secret" in seen_payloads[0]
    assert "client_secret" not in seen_payloads[1]


def test_kakao_notifier_run_local_auth_flow_receives_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN", "KAKAO_REST_API_KEY", "KAKAO_REDIRECT_URI", "KAKAO_CLIENT_SECRET", "YTA_KAKAO_ACCESS_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "KAKAO_REST_API_KEY=rest-key",
                "KAKAO_REDIRECT_URI=http://127.0.0.1:8519",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    notifier = KakaoNotifier(project_root=tmp_path)
    monkeypatch.setattr(notifier, "authorization_url", lambda: "https://example.com/auth")
    monkeypatch.setattr(notifier, "exchange_authorization_code", lambda code: {"access_token": "x", "refresh_token": "y", "code": code})

    def send_callback() -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                urlopen("http://127.0.0.1:8519/?code=test-code", timeout=5).read()
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("callback server did not accept the Kakao authorization code in time")

    timer = threading.Timer(0.3, send_callback)
    timer.start()
    try:
        payload = notifier.run_local_auth_flow(timeout_seconds=5, open_browser=False)
    finally:
        timer.cancel()

    assert payload["code"] == "test-code"
