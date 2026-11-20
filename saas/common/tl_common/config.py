"""Config loading shared by SaaS services."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))


def load_yaml(name: str) -> dict:
    p = Path(name) if os.path.isabs(name) else CONFIG_DIR / name
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def tenants_file() -> dict:
    return load_yaml(os.environ.get("TENANTS_FILE", str(CONFIG_DIR / "tenants.yaml")))


def home_region(tenant: str) -> str | None:
    return ((tenants_file().get("tenants") or {}).get(tenant) or {}).get("home_region")


def iso(dt: datetime | float | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt, tz=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
