"""Collector settings: environment + /config/*.yaml (Tier 1 only)."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VERSION = "0.3.1"


def _yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    # ${VAR} interpolation from the environment (organiser_mapping.yaml uses it)
    text = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), text)
    return yaml.safe_load(text) or {}


@dataclass
class Settings:
    site: str
    collector_id: str
    region: str
    src_tz: str
    ingest_base: str
    control_url: str
    simulator_url: str
    enrol_mode: str
    enrol_token: str
    hmac_key: bytes
    data_dir: Path
    config_dir: Path
    cert_dir: Path
    ca_file: Path
    window_s: int
    batch_ms: int
    heartbeat_s: int
    warmup_s: int
    cfg: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    topology: dict = field(default_factory=dict)
    organiser: dict = field(default_factory=dict)
    config_hash: str = "000000000000"
    push_port: int = 8081
    syslog_port: int = 5514
    ui_port: int = 9090

    @property
    def client_cert(self) -> tuple[str, str]:
        return str(self.cert_dir / "client.crt"), str(self.cert_dir / "client.key")

    def threshold_for(self, metric: str) -> dict | None:
        sig = (self.thresholds.get("signals") or {}).get(metric)
        if not sig:
            return None
        override = ((self.thresholds.get("overrides") or {}).get(self.site) or {}).get(metric) or {}
        return {**sig, **override}


def load() -> Settings:
    env = os.environ.get
    config_dir = Path(env("CONFIG_DIR", "/config"))
    cfg = _yaml(config_dir / "collector.yaml")
    key_hex = env("HMAC_KEY", "")
    if not key_hex or "change-me" in key_hex:
        raise SystemExit("HMAC_KEY missing - run `python scripts/tl.py init`")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        key = key_hex.encode()
    raw_cfg = b"".join((config_dir / n).read_bytes() for n in ("collector.yaml", "thresholds.yaml")
                       if (config_dir / n).exists())
    s = Settings(
        site=env("SITE", "acme"),
        collector_id=env("COLLECTOR_ID", "col-ac01"),
        region=env("REGION", "ap-south"),
        src_tz=env("SRC_TZ", "+05:30"),
        ingest_base=env("INGEST_BASE", "https://nginx-ap:8443").rstrip("/"),
        control_url=env("CONTROL_URL", "https://control:8444").rstrip("/"),
        simulator_url=env("SIMULATOR_URL", "http://simulator:8090").rstrip("/"),
        enrol_mode=env("ENROL_MODE", "preissued"),
        enrol_token=env("ENROL_TOKEN", ""),
        hmac_key=key,
        data_dir=Path(env("DATA_DIR", "/data")),
        config_dir=config_dir,
        cert_dir=Path(env("CERT_DIR", "/certs/client")),
        ca_file=Path(env("CA_FILE", "/certs/ca.crt")),
        window_s=int(env("WINDOW_SECONDS", cfg.get("window_s", 5))),
        batch_ms=int(env("BATCH_INTERVAL_MS", cfg.get("batch_ms", 1000))),
        heartbeat_s=int(env("HEARTBEAT_SECONDS", cfg.get("heartbeat_s", 5))),
        warmup_s=int(env("WARMUP_SECONDS", 300)),
        cfg=cfg,
        thresholds=_yaml(config_dir / "thresholds.yaml"),
        topology=_yaml(Path(env("SITE_DIR", str(config_dir / "site"))) / "topology.yaml"),
        organiser=_yaml(config_dir / "organiser_mapping.yaml"),
        config_hash=hashlib.sha256(raw_cfg).hexdigest()[:12],
    )
    if s.enrol_mode == "token":
        s.cert_dir = s.data_dir / "pki"
        s.ca_file = s.data_dir / "pki" / "ca.crt"
    return s
