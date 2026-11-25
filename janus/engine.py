from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import List, Optional

from . import gate, indicators, risk
from .marketdata import analysis_symbol
from .models import Decision, Direction, Horizon, Kind, Position, Signal, now_utc
from .store import Store

log = logging.getLogger("janus.engine")


class Engine:
    def __init__(self, cfg, store: Store, feed, broker=None, notifier=None):
        self.cfg = cfg
        self.store = store
        self.feed = feed
        self.broker = broker
        self.notifier = notifier
        self.mode = cfg.get("mode", "shadow")

    # -- helpers ------------------------------------------------------------
    def _snapshot(self, signal: Signal) -> Optional[dict]:
        sym = analysis_symbol(signal.instrument)
        df = self.feed.history(sym)
        if df is None or df.empty or len(df) < 55:
            return None
        return indicators.snapshot(df)

    def _entry_window_ok(self, signal: Signal) -> bool:
        if not signal.posted_at:
            return True
        try:
            posted = dt.datetime.fromisoformat(str(signal.posted_at).replace("Z", "+00:00"))
        except ValueError:
            return True
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=dt.timezone.utc)
        minutes = float(self.cfg.get(f"gate.entry_validity_minutes.{signal.horizon.value}", 1440))
        return (dt.datetime.now(dt.timezone.utc) - posted).total_seconds() <= minutes * 60

    def _realized_today(self) -> float:
        total = 0.0
        today = dt.date.today().isoformat()
        for p in self.store.all_positions():
            if p.status == "CLOSED" and p.closed_at and p.closed_at[:10] == today:
                total += p.realized_pnl
        return total

    # -- main ---------------------------------------------------------------
    def process(self, signal: Signal) -> Decision:
        added = self.store.save_signal(signal)
        if not added:
            return Decision(signal_key=signal.key, action="SKIP", score=0.0, passed=False,
                            reasons=["duplicate signal"])

        if signal.kind == Kind.ENTRY:
            return self._handle_entry(signal)
        if signal.kind == Kind.EXIT:
            return self._handle_exit(signal)
        return self._handle_modify(signal)

    def _handle_entry(self, signal: Signal) -> Decision:
        if Path(self.cfg.get("kill_switch_path", "data/KILL")).exists():
            dec = Decision(signal_key=signal.key, action="SKIP", score=0.0, passed=False,
                           reasons=["kill switch active"])
            self.store.save_decision(dec)
            return dec

        snap = self._snapshot(signal)
        if snap is None:
            dec = Decision(signal_key=signal.key, action="SKIP", score=0.0, passed=False,
                           reasons=["no market data for underlying"])
            self.store.save_decision(dec)
            return dec

        if not self._entry_window_ok(signal):
            dec = Decision(signal_key=signal.key, action="SKIP", score=0.0, passed=False,
                           reasons=["entry window expired"], features={"snapshot": snap})
            self.store.save_decision(dec)
            return dec

        res = gate.evaluate(signal, snap, self.cfg)
        if not res.passed:
            dec = Decision(signal_key=signal.key, action="SKIP", score=res.score, passed=False,
                           reasons=res.reasons, features=res.features, rr=res.rr)
            self.store.save_decision(dec)
            self._log_decision(dec)
            return dec

        guard = risk.portfolio_guard(self.store.open_positions(), self.cfg, signal.instrument,
                                     self._realized_today(), float(self.cfg.get("capital", 0)))
        if not guard.allowed:
            dec = Decision(signal_key=signal.key, action="SKIP", score=res.score, passed=False,
                           reasons=[guard.reason] + res.reasons, features=res.features, rr=res.rr)
            self.store.save_decision(dec)
            return dec

        lot = signal.instrument.lot_size
        rv = risk.size_position(float(self.cfg.get("capital", 0)), float(self.cfg.get("risk.per_trade_pct", 0.005)),
                                res.entry_limit, res.sl, lot_size=lot, price=snap.get("price"))
        if not rv.allowed:
            dec = Decision(signal_key=signal.key, action="SKIP", score=res.score, passed=False,
                           reasons=[rv.reason] + res.reasons, features=res.features, rr=res.rr)
            self.store.save_decision(dec)
            return dec

        pos = Position(
            position_id=Position.new_id(),
            signal_key=signal.key,
            instrument=signal.instrument,
            direction=signal.instrument.direction,
            qty=rv.qty,
            entry_price=float(res.entry_limit),
            stop_loss=float(res.sl),
            target=float(res.target),
            thesis={
                "sma50": snap.get("sma50"),
                "atr": snap.get("atr14"),
                "adx": snap.get("adx14"),
                "horizon": signal.horizon.value,
                "univest_entry": signal.entry_ref,
                "sl_atr": res.features.get("sl_atr"),
            },
        )

        dec = Decision(signal_key=signal.key, action="ENTER", score=res.score, passed=True,
                       reasons=res.reasons, features=res.features, size=rv.qty,
                       entry_limit=res.entry_limit, sl=res.sl, target=res.target, rr=res.rr)

        if self.mode == "live" and self.broker is not None:
            order = self.broker.place_order(signal.instrument, signal.instrument.direction, rv.qty,
                                            limit=res.entry_limit)
            self.store.save_order(order)
            if order.status == "REJECTED":
                dec.action = "SKIP"
                dec.reasons.append(f"broker rejected: {order.message}")
                self.store.save_decision(dec)
                return dec
        else:
            from .execution.paper import PaperBroker
            order = PaperBroker().place_entry(pos.position_id, signal.instrument, signal.instrument.direction,
                                              rv.qty, res.entry_limit, snap.get("price", res.entry_limit))
            self.store.save_order(order)
            if order.status != "FILLED":
                dec.action = "SKIP"
                dec.reasons.append(order.message)
                self.store.save_decision(dec)
                return dec
            dec.features["fill_price"] = order.price

        self.store.upsert_position(pos)
        self.store.save_decision(dec)
        self._log_decision(dec)
        self._notify(f"JANUS ENTER {signal.instrument.underlying} {signal.instrument.direction.value} "
                     f"qty={rv.qty} @ {res.entry_limit} SL {res.sl} T {res.target} score={res.score:.2f}")
        return dec

    def _handle_exit(self, signal: Signal) -> Decision:
        open_pos = [p for p in self.store.open_positions()
                    if p.instrument.underlying == signal.instrument.underlying]
        if not open_pos:
            dec = Decision(signal_key=signal.key, action="SKIP", score=1.0, passed=True,
                           reasons=["exit signal but no open position"])
            self.store.save_decision(dec)
            return dec

        snap = self._snapshot(signal)
        price = (snap or {}).get("price")
        reasons: List[str] = []
        for pos in open_pos:
            if price is None:
                reasons.append("no price; holding, will retry")
                continue
            r = pos.r_multiple(price)
            thesis_ok = self._thesis_intact(pos, snap)
            if not thesis_ok:
                self._close(pos, price, f"thesis broken (exit signal)")
                reasons.append(f"{pos.instrument.underlying}: exited, thesis broken")
            elif r >= 1.0:
                self._trail(pos, snap)
                reasons.append(f"{pos.instrument.underlying}: thesis intact at {r:.2f}R — holding with trail")
            else:
                self._close(pos, price, "exit signal, not in profit")
                reasons.append(f"{pos.instrument.underlying}: exited ({r:.2f}R)")

        dec = Decision(signal_key=signal.key, action="EXIT", score=1.0, passed=True, reasons=reasons)
        self.store.save_decision(dec)
        self._log_decision(dec)
        return dec

    def _handle_modify(self, signal: Signal) -> Decision:
        dec = Decision(signal_key=signal.key, action="MODIFY", score=1.0, passed=True,
                       reasons=["modify/update noted (no automatic stop change)"])
        self.store.save_decision(dec)
        return dec

    # -- thesis & position management --------------------------------------
    def _thesis_intact(self, pos: Position, snap: Optional[dict]) -> bool:
        if not snap:
            return False
        px = snap.get("price")
        sma50 = pos.thesis.get("sma50") or snap.get("sma50")
        adx = snap.get("adx14")
        min_adx = float(self.cfg.get("gate.min_adx", 20))
        if px is None:
            return False
        if pos.direction == Direction.LONG:
            if sma50 is not None and px < sma50:
                return False
        else:
            if sma50 is not None and px > sma50:
                return False
        if adx is not None and adx < min_adx:
            return False
        return True

    def _trail(self, pos: Position, snap: dict):
        atr = snap.get("atr14") or pos.thesis.get("atr")
        if not atr:
            return
        mult = float(self.cfg.get("exit.trail_atr_mult", 2.0))
        px = snap.get("price")
        if pos.direction == Direction.LONG:
            new_sl = max(pos.stop_loss, px - mult * atr)
        else:
            new_sl = min(pos.stop_loss, px + mult * atr)
        if new_sl != pos.stop_loss:
            pos.stop_loss = round(new_sl, 4)
            pos.stop_moved_to_be = True
            self.store.upsert_position(pos)

    def _close(self, pos: Position, price: float, reason: str):
        if pos.direction == Direction.LONG:
            pnl = (price - pos.entry_price) * pos.qty
        else:
            pnl = (pos.entry_price - price) * pos.qty
        pos.status = "CLOSED"
        pos.exit_price = price
        pos.closed_at = now_utc()
        pos.realized_pnl = round(pnl, 2)
        self.store.upsert_position(pos)
        self._notify(f"JANUS EXIT {pos.instrument.underlying} @ {price} pnl={pnl:.0f} ({reason})")

    def _log_decision(self, dec: Decision):
        log.info("%s %s score=%.2f %s", dec.action, dec.signal_key, dec.score,
                 "; ".join(dec.reasons[:3]))

    def _notify(self, message: str):
        if not self.notifier:
            return
        try:
            self.notifier(message)
        except Exception as e:
            log.warning("notify failed: %s", e)
