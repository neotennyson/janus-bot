from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from .models import Decision, Order, Position, Signal, now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    key TEXT PRIMARY KEY,
    received_at TEXT,
    posted_at TEXT,
    kind TEXT,
    segment TEXT,
    source TEXT,
    payload TEXT,
    raw_text TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_key TEXT,
    created_at TEXT,
    action TEXT,
    passed INTEGER,
    score REAL,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    signal_key TEXT,
    status TEXT,
    opened_at TEXT,
    closed_at TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    position_id TEXT,
    created_at TEXT,
    status TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    kind TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    equity REAL
);
"""


class Store:
    def __init__(self, path: str = "data/janus.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def log_event(self, kind: str, payload: dict):
        self.conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?,?,?)",
            (now_utc(), kind, json.dumps(payload, default=str)),
        )
        self.conn.commit()

    def save_signal(self, sig: Signal) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO signals (key, received_at, posted_at, kind, segment, source, payload, raw_text)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                sig.key,
                sig.received_at,
                sig.posted_at,
                sig.kind.value,
                sig.instrument.segment.value,
                sig.source,
                json.dumps(sig.to_dict(), default=str),
                sig.raw_text,
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def save_decision(self, dec: Decision):
        self.conn.execute(
            "INSERT INTO decisions (signal_key, created_at, action, passed, score, payload) VALUES (?,?,?,?,?,?)",
            (dec.signal_key, dec.created_at, dec.action, int(dec.passed), dec.score, json.dumps(dec.to_dict(), default=str)),
        )
        self.conn.commit()

    def upsert_position(self, pos: Position):
        self.conn.execute(
            "INSERT OR REPLACE INTO positions (position_id, signal_key, status, opened_at, closed_at, payload)"
            " VALUES (?,?,?,?,?,?)",
            (pos.position_id, pos.signal_key, pos.status, pos.opened_at, pos.closed_at, json.dumps(pos.to_dict(), default=str)),
        )
        self.conn.commit()

    def save_order(self, order: Order):
        self.conn.execute(
            "INSERT OR REPLACE INTO orders (order_id, position_id, created_at, status, payload) VALUES (?,?,?,?,?)",
            (order.order_id, order.position_id, order.created_at, order.status, json.dumps(order.to_dict(), default=str)),
        )
        self.conn.commit()

    def open_positions(self) -> List[Position]:
        rows = self.conn.execute("SELECT payload FROM positions WHERE status='OPEN'").fetchall()
        return [Position.from_dict(json.loads(r["payload"])) for r in rows]

    def all_positions(self) -> List[Position]:
        rows = self.conn.execute("SELECT payload FROM positions ORDER BY opened_at").fetchall()
        return [Position.from_dict(json.loads(r["payload"])) for r in rows]

    def get_position(self, position_id: str) -> Optional[Position]:
        row = self.conn.execute("SELECT payload FROM positions WHERE position_id=?", (position_id,)).fetchone()
        return Position.from_dict(json.loads(row["payload"])) if row else None

    def signal_seen(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM signals WHERE key=?", (key,)).fetchone() is not None

    def decisions_for(self, signal_key: str) -> List[dict]:
        rows = self.conn.execute(
            "SELECT payload FROM decisions WHERE signal_key=? ORDER BY id DESC", (signal_key,)
        ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def record_equity(self, equity: float):
        self.conn.execute("INSERT INTO equity (ts, equity) VALUES (?,?)", (now_utc(), equity))
        self.conn.commit()
