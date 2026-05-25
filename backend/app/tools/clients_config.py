"""Loader for ``clients.toml``."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENTS_TOML = REPO_ROOT / "clients.toml"


@dataclass(frozen=True)
class WebCfg:
    url: str
    login_mode: str
    browser: str
    headless: bool


@dataclass(frozen=True)
class DesktopCfg:
    exe: str
    window_title_regex: str
    startup_seconds: int
    server_location: str = ""


@dataclass(frozen=True)
class StreamCfg:
    fps: int
    jpeg_quality: int


@dataclass(frozen=True)
class ClientsConfig:
    web: WebCfg
    unity: DesktopCfg
    studio: DesktopCfg
    stream: StreamCfg


@lru_cache
def load_clients_config(path: Path | None = None) -> ClientsConfig:
    p = path or CLIENTS_TOML
    if not p.exists():
        raise FileNotFoundError(f"clients.toml not found at {p}")
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    return ClientsConfig(
        web=WebCfg(**raw["web"]),
        unity=DesktopCfg(**raw["unity"]),
        studio=DesktopCfg(**raw["studio"]),
        stream=StreamCfg(**raw["stream"]),
    )
