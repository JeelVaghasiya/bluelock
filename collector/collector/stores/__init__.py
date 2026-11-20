"""Local stores. outbox.db is the 'core' connection with egress.db and state.db ATTACHed,
so sealing a payload (seq++, outbox insert, egress 'sealed' entry) is ONE atomic transaction:
a crash can never leave a sequence number without its payload or its audit entry."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import egress as _eg
from . import outbox as _ob
from . import state as _st
from .egress import EgressLog
from .outbox import Outbox
from .raw import RawStore
from .sqlite import Db
from .state import StateStore


@dataclass
class Stores:
    raw: RawStore
    egress: EgressLog
    outbox: Outbox
    state: StateStore
    core: Db


def open_stores(data_dir: str | Path) -> Stores:
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    # create schemas in their own files first
    for name, schema in (("egress.db", _eg.SCHEMA), ("state.db", _st.SCHEMA)):
        c = sqlite3.connect(d / name)
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(schema)
        c.close()
    core = Db(d / "outbox.db", _ob.SCHEMA)
    with core.lock:
        core.conn.execute("ATTACH DATABASE ? AS eg", (str(d / "egress.db"),))
        core.conn.execute("ATTACH DATABASE ? AS st", (str(d / "state.db"),))
        core.conn.execute("PRAGMA eg.journal_mode=WAL")
        core.conn.execute("PRAGMA st.journal_mode=WAL")
    return Stores(raw=RawStore(str(d / "raw.db")), egress=EgressLog(core), outbox=Outbox(core),
                  state=StateStore(core), core=core)


__all__ = ["Stores", "open_stores", "RawStore", "EgressLog", "Outbox", "StateStore", "Db"]
