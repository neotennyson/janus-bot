from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

from ..normalize import from_json
from .notifications import parse_notification

log = logging.getLogger("janus.webhook")


def create_app(engine, secret: str = "", monitor=None) -> FastAPI:
    app = FastAPI(title="JANUS signal intake", version="0.2.0")
    kill_path = Path(engine.cfg.get("kill_switch_path", "data/KILL"))

    def _check(provided: str):
        if secret and provided != secret:
            raise HTTPException(status_code=401, detail="bad secret")

    @app.get("/health")
    def health():
        return {"ok": True, "mode": engine.mode, "kill": kill_path.exists()}

    @app.post("/signal")
    async def signal(request: Request, x_janus_secret: str = Header(default="")):
        _check(x_janus_secret)
        payload = await request.json()
        if isinstance(payload, dict) and payload.get("text") and "symbol" not in payload and "signal" not in payload:
            kind, sig = parse_notification(str(payload["text"]))
            if kind == "trigger":
                if monitor:
                    monitor.wake()
                return {"action": "TRIGGER", "detail": "woke monitor"}
        else:
            sig = from_json(payload, source="webhook")
        if sig is None:
            raise HTTPException(status_code=422, detail="could not parse signal")
        return engine.process(sig).to_dict()

    @app.post("/signal/text")
    async def signal_text(request: Request, x_janus_secret: str = Header(default="")):
        _check(x_janus_secret)
        text = (await request.body()).decode()
        kind, sig = parse_notification(text, source="webhook")
        if kind == "trigger":
            if monitor:
                monitor.wake()
            return {"action": "TRIGGER", "detail": "woke monitor"}
        return engine.process(sig).to_dict()

    @app.post("/trigger")
    async def trigger(x_janus_secret: str = Header(default="")):
        _check(x_janus_secret)
        if monitor:
            monitor.wake()
        return {"ok": True, "detail": "monitor woken"}

    # -- read-only API ------------------------------------------------------
    @app.get("/api/signals")
    def api_signals(limit: int = 50):
        rows = engine.store.conn.execute(
            "SELECT key, received_at, kind, segment, payload FROM signals ORDER BY received_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"key": r["key"], "received_at": r["received_at"], "kind": r["kind"],
                 "segment": r["segment"], "payload": json.loads(r["payload"])} for r in rows]

    @app.get("/api/decisions")
    def api_decisions(limit: int = 50):
        rows = engine.store.conn.execute(
            "SELECT created_at, action, passed, score, payload FROM decisions ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    @app.get("/api/positions")
    def api_positions(open_only: bool = False):
        return [p.to_dict() for p in (engine.store.open_positions() if open_only else engine.store.all_positions())]

    @app.post("/api/kill")
    def api_kill(x_janus_secret: str = Header(default="")):
        _check(x_janus_secret)
        kill_path.parent.mkdir(parents=True, exist_ok=True)
        kill_path.write_text("killed")
        return {"ok": True, "kill": True}

    @app.post("/api/resume")
    def api_resume(x_janus_secret: str = Header(default="")):
        _check(x_janus_secret)
        kill_path.unlink(missing_ok=True)
        return {"ok": True, "kill": False}

    return app
