from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional

from .models import Direction, Horizon, Segment, Signal


@dataclass
class GateResult:
    passed: bool
    score: float
    reasons: List[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    entry_limit: Optional[float] = None
    sl: Optional[float] = None
    target: Optional[float] = None
    rr: Optional[float] = None


def _nearest_target(sig: Signal, direction: Direction, entry: float) -> Optional[float]:
    if not sig.targets:
        return None
    if direction == Direction.LONG:
        beyond = [t for t in sig.targets if t > entry]
        return min(beyond) if beyond else max(sig.targets)
    beyond = [t for t in sig.targets if t < entry]
    return max(beyond) if beyond else min(sig.targets)


def evaluate(signal: Signal, snap: dict, cfg, price: Optional[float] = None) -> GateResult:
    reasons: List[str] = []
    features: dict = {"snapshot": snap}
    inst = signal.instrument

    def reject(reason: str, score: float = 0.0) -> GateResult:
        reasons.append(reason)
        return GateResult(passed=False, score=score, reasons=reasons, features=features)

    allowed = cfg.get("segments", ["FUTURES", "OPTIONS", "COMMODITY"])
    if inst.segment.value not in allowed:
        return reject(f"segment {inst.segment.value} not enabled")

    if signal.kind.value != "ENTRY":
        return reject("not an entry signal")

    entry = signal.entry_ref
    if entry is None:
        return reject("no entry price stated")
    if signal.stop_loss is None:
        return reject("no stop loss stated — refusing to trade blind")
    target = _nearest_target(signal, inst.direction, entry)
    if target is None:
        return reject("no target stated")

    sl = float(signal.stop_loss)
    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0:
        return reject("stop loss equals entry (zero risk distance)")

    reward_per_unit = abs(target - entry)
    rr = reward_per_unit / risk_per_unit
    features["rr"] = round(rr, 2)
    min_rr = float(cfg.get("gate.min_rr", 1.5))
    if rr < min_rr:
        return reject(f"reward:risk {rr:.2f} < {min_rr}")

    px = float(price if price is not None else snap.get("price", entry))
    features["underlying_price"] = round(px, 4)

    # Only for NSE futures is the underlying proxy price comparable to the
    # signal's stated entry. Options quote a PREMIUM and MCX quotes a contract
    # in different units, so price-band / ATR checks must use a real broker
    # quote (deferred), never the underlying proxy.
    comparable = inst.segment == Segment.FUTURES and inst.exchange == "NSE"
    features["price_comparable"] = comparable

    if comparable:
        max_chase = float(cfg.get("gate.max_chase_pct", 0.003))
        deep = float(cfg.get("gate.deep_discount_pct", 0.03))
        if inst.direction == Direction.LONG:
            if px > entry * (1 + max_chase):
                return reject(f"price ran away ({px:.2f} > entry {entry:.2f} + {max_chase:.2%}) — no chasing")
            if px < entry * (1 - deep):
                return reject(f"price {px:.2f} is >{deep:.0%} below entry {entry:.2f} — move missed/invalidated")
        else:
            if px < entry * (1 - max_chase):
                return reject(f"price ran away ({px:.2f} < entry {entry:.2f}) — no chasing")
            if px > entry * (1 + deep):
                return reject(f"price {px:.2f} is >{deep:.0%} above entry {entry:.2f} — move missed/invalidated")
    else:
        reasons.append(f"{inst.segment.value} price-band check deferred to broker quote")

    atr = snap.get("atr14")
    if comparable and atr and atr == atr:
        ratio = risk_per_unit / atr
        features["sl_atr"] = round(ratio, 2)
        max_sl_atr = float(cfg.get("gate.max_sl_atr", 1.5))
        if ratio > max_sl_atr:
            return reject(f"stop {ratio:.2f}x ATR is too wide (> {max_sl_atr}x)")

    adx = snap.get("adx14")
    if adx is not None:
        features["adx14"] = round(adx, 2)
        if adx < float(cfg.get("gate.min_adx", 20)):
            return reject(f"ADX {adx:.1f} < {cfg.get('gate.min_adx', 20)} — choppy, no trend")

    if inst.direction == Direction.LONG and int(signal.horizon.value in ("SWING", "POSITIONAL", "WEEKLY")):
        sma50 = snap.get("sma50")
        if sma50 is not None and px < sma50 and snap.get("sma200") and sma50 < snap.get("sma200"):
            return reject("long signal against a downtrend (price < SMA50 < SMA200)")
    if inst.direction == Direction.SHORT:
        sma50 = snap.get("sma50")
        if sma50 is not None and px > sma50 and snap.get("sma200") and sma50 > snap.get("sma200"):
            return reject("short signal against an uptrend (price > SMA50 > SMA200)")

    rsi = snap.get("rsi14")
    if rsi is not None:
        features["rsi14"] = round(rsi, 1)
        if inst.direction == Direction.LONG and rsi > 80:
            return reject(f"RSI {rsi:.0f} overbought — poor long entry")
        if inst.direction == Direction.SHORT and rsi < 20:
            return reject(f"RSI {rsi:.0f} oversold — poor short entry")

    if cfg.get("gate.block_expiry_day", False) and inst.expiry:
        try:
            if dt.date.fromisoformat(str(inst.expiry)[:10]) == dt.date.today():
                return reject("expiry-day entry blocked")
        except ValueError:
            pass

    score, parts = _score(signal, snap, px, entry, rr, atr, comparable)
    features["subscores"] = parts
    min_score = float(cfg.get("gate.min_score", 0.55))
    passed = score >= min_score
    if passed:
        reasons.append(f"score {score:.2f} >= {min_score:.2f}; all hard filters passed")
    else:
        reasons.append(f"score {score:.2f} < {min_score:.2f}")

    if not comparable:
        entry_limit = entry
    elif inst.direction == Direction.LONG:
        entry_limit = min(entry, px)
    else:
        entry_limit = max(entry, px)

    return GateResult(
        passed=passed,
        score=round(score, 4),
        reasons=reasons,
        features=features,
        entry_limit=round(entry_limit, 4),
        sl=sl,
        target=target,
        rr=round(rr, 3),
    )


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _score(signal: Signal, snap: dict, px: float, entry: float, rr: float, atr, comparable: bool) -> tuple:
    d = 1.0 if signal.instrument.direction == Direction.LONG else -1.0

    trend = 0.0
    for key, w in (("ema20", 0.4), ("sma50", 0.35), ("sma200", 0.25)):
        level = snap.get(key)
        if level is not None and level == level:
            trend += w * (1.0 if (px - level) * d > 0 else 0.0)
    trend = _clamp01(trend)

    rsi = snap.get("rsi14") or 50.0
    if d > 0:
        momentum = _clamp01((rsi - 45) / 25.0) if rsi <= 80 else 0.0
    else:
        momentum = _clamp01((55 - rsi) / 25.0) if rsi >= 20 else 0.0
    macd_hist = snap.get("macd_hist") or 0.0
    momentum = _clamp01(0.7 * momentum + 0.3 * (1.0 if macd_hist * d > 0 else 0.0))

    strength = _clamp01(((snap.get("adx14") or 0.0) - 15) / 25.0)

    rr_score = _clamp01((rr - 1.0) / 2.0)

    if comparable and atr and atr == atr and atr > 0:
        dist = abs(px - entry) / atr
        entry_q = _clamp01(1.0 - dist / 1.0)
    else:
        entry_q = 0.5

    vol_rel = snap.get("vol_rel")
    if vol_rel is None or vol_rel != vol_rel or vol_rel <= 0:
        volume = 0.5
    else:
        volume = _clamp01((vol_rel - 0.8) / 0.7)

    parts = {
        "trend": round(trend, 3),
        "momentum": round(momentum, 3),
        "strength": round(strength, 3),
        "rr": round(rr_score, 3),
        "entry_quality": round(entry_q, 3),
        "volume": round(volume, 3),
    }
    weights = {"trend": 0.25, "momentum": 0.20, "strength": 0.15, "rr": 0.20, "entry_quality": 0.10, "volume": 0.10}
    score = sum(parts[k] * weights[k] for k in weights)
    return score, parts
