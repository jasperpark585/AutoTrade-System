import logging
from pathlib import Path

from app.runtime.tunnel import CloudflareTunnel


def test_named_tunnel_command_uses_hostname_and_token(tmp_path: Path) -> None:
    tunnel = CloudflareTunnel(
        tmp_path,
        code_root=tmp_path,
        local_url="http://127.0.0.1:8501",
        logger=logging.getLogger("test"),
        enabled=True,
        mode="named",
        tunnel_name="studio-fixed",
        hostname="studio.example.com",
        tunnel_token="token-value",
    )
    tunnel.binary = Path("cloudflared.exe")

    command = tunnel._named_tunnel_command()

    assert command is not None
    assert "--hostname" in command
    assert "studio.example.com" in command
    assert "--token" in command
    assert "token-value" in command


def test_named_tunnel_command_requires_hostname_and_token(tmp_path: Path) -> None:
    tunnel = CloudflareTunnel(
        tmp_path,
        code_root=tmp_path,
        local_url="http://127.0.0.1:8501",
        logger=logging.getLogger("test"),
        enabled=True,
        mode="named",
        tunnel_name="studio-fixed",
        hostname="",
        tunnel_token="",
    )
    tunnel.binary = Path("cloudflared.exe")

    assert tunnel._named_tunnel_command() is None
