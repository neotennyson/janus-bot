from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_config
from .engine import Engine
from .marketdata import DataFeed
from .monitor import Monitor
from .normalize import from_json, from_text
from .store import Store


def _notifier(cfg):
    topic = cfg.get("notify.ntfy_topic", "")
    if not topic:
        return None

    def send(msg: str):
        import urllib.request
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=msg.encode(), method="POST"), timeout=10)
        except Exception:
            pass

    return send


def build_engine(cfg_path: str) -> Engine:
    cfg = load_config(cfg_path)
    store = Store(cfg.get("store.path", "data/janus.db"))
    feed = DataFeed(cache_dir=cfg.get("data.cache_dir", "data/cache"),
                    provider=cfg.get("data.provider", "yfinance"),
                    lookback_days=int(cfg.get("data.lookback_days", 400)))

    broker = None
    if cfg.get("mode") == "live":
        from .execution.dhan import DhanBroker
        broker = DhanBroker(cfg.get("broker.client_id", ""), cfg.get("broker.access_token", ""))
        if not broker.validate():
            raise SystemExit("Dhan auth failed — refusing to start in live mode")

    return Engine(cfg, store, feed, broker=broker, notifier=_notifier(cfg))


def build_web(cfg):
    from .sources.web import UnivestWeb
    return UnivestWeb(
        base_url=cfg.get("sources.web.base_url", "https://univest.in"),
        ideas_path=cfg.get("sources.web.ideas_path", "/intraday-stocks-for-today"),
        state_path=cfg.get("sources.web.state_path", "data/univest_state.json"),
        captured_dir=cfg.get("sources.web.captured_dir", "data/captured"),
        api_hint=cfg.get("sources.web.api_hint", ""),
    )


def build_monitor(eng: Engine) -> Monitor:
    sources = []
    if eng.cfg.get("sources.web.enabled", False):
        sources.append(build_web(eng.cfg))
    interval = int(eng.cfg.get("sources.web.poll_interval_seconds", 20))
    return Monitor(eng, sources, interval=interval)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="janus")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_text = sub.add_parser("ingest-text", help="ingest a raw UNIVEST message")
    p_text.add_argument("text")

    p_json = sub.add_parser("ingest-json", help="ingest a structured signal JSON")
    p_json.add_argument("json")
    p_json.add_argument("--source", default="manual")

    p_file = sub.add_parser("replay", help="replay a JSONL file of signals")
    p_file.add_argument("file")

    p_serve = sub.add_parser("serve", help="run the webhook + background monitor")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)

    sub.add_parser("monitor", help="run the polling loop in the foreground")
    sub.add_parser("web-login", help="open a browser to log into univest.in and save the session")
    sub.add_parser("web-discover", help="record univest.in's private JSON responses")
    sub.add_parser("web-poll", help="poll univest.in once and print the signals found")
    sub.add_parser("positions", help="list positions")
    sub.add_parser("decisions", help="list recent decisions")

    args = ap.parse_args(argv)

    if args.cmd in ("web-login", "web-discover"):
        cfg = load_config(args.config)
        web = build_web(cfg)
        if args.cmd == "web-login":
            web.login()
        else:
            web.discover()
        return

    if args.cmd == "serve":
        import uvicorn
        from .sources.webhook import create_app
        eng = build_engine(args.config)
        mon = build_monitor(eng)
        if mon.sources:
            mon.start_background()
        app = create_app(eng, secret=eng.cfg.get("webhook.secret", ""), monitor=mon)
        uvicorn.run(app, host=args.host or eng.cfg.get("webhook.host", "127.0.0.1"),
                    port=args.port or int(eng.cfg.get("webhook.port", 8787)))
        return

    eng = build_engine(args.config)

    if args.cmd == "monitor":
        build_monitor(eng).run_forever()
    elif args.cmd == "web-poll":
        for sig in build_web(eng.cfg).poll():
            print(json.dumps(sig.to_dict(), indent=2, default=str))
            print("->", eng.process(sig).action)
    elif args.cmd == "ingest-text":
        sig = from_text(args.text, source="manual")
        if not sig:
            print("could not parse"); sys.exit(2)
        print(json.dumps({"signal": sig.to_dict(), "decision": eng.process(sig).to_dict()},
                         indent=2, default=str))
    elif args.cmd == "ingest-json":
        sig = from_json(json.loads(args.json), source=args.source)
        print(json.dumps(eng.process(sig).to_dict(), indent=2, default=str))
    elif args.cmd == "replay":
        for line in Path(args.file).read_text().splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            sig = from_json(obj, source=obj.get("source", "replay"))
            dec = eng.process(sig)
            print(dec.action, sig.instrument.underlying, dec.score, "; ".join(dec.reasons[:1]))
    elif args.cmd == "positions":
        for p in eng.store.all_positions():
            print(f"{p.status:6} {p.instrument.underlying:12} {p.direction.value:5} qty={p.qty:<8} "
                  f"entry={p.entry_price} sl={p.stop_loss} tgt={p.target} pnl={p.realized_pnl}")
    elif args.cmd == "decisions":
        rows = eng.store.conn.execute(
            "SELECT created_at, action, score, payload FROM decisions ORDER BY id DESC LIMIT 30").fetchall()
        for r in rows:
            d = json.loads(r["payload"])
            print(f"{r['created_at'][:19]} {r['action']:6} score={r['score']:.2f} "
                  f"{(d.get('reasons') or [''])[0]}")


if __name__ == "__main__":
    main()
