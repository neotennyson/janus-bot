from __future__ import annotations

import re
import uuid
from typing import Optional

from .models import Direction, Horizon, Instrument, Kind, OptionType, Segment, Signal

# ---------------------------------------------------------------------------
# JSON path: the webhook / Android bridge sends structured fields.
# ---------------------------------------------------------------------------

_SEGMENT_ALIASES = {
    "FUT": Segment.FUTURES,
    "FUTURE": Segment.FUTURES,
    "FUTURES": Segment.FUTURES,
    "OPT": Segment.OPTIONS,
    "OPTION": Segment.OPTIONS,
    "OPTIONS": Segment.OPTIONS,
    "COMMODITY": Segment.COMMODITY,
    "COMMODITIES": Segment.COMMODITY,
    "MCX": Segment.COMMODITY,
    "EQUITY": Segment.EQUITY,
    "CASH": Segment.EQUITY,
}


def _f(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group()) if m else None


def _targets(value) -> list:
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str):
        return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", value.replace(",", ""))]
    return [float(v) for v in value if _f(v) is not None]


def from_json(payload: dict, source: str = "webhook") -> Signal:
    if isinstance(payload.get("signal"), dict):
        payload = payload["signal"]
    seg_raw = str(payload.get("segment", "FUTURES")).upper()
    segment = _SEGMENT_ALIASES.get(seg_raw, Segment.FUTURES)
    direction = Direction.SHORT if str(payload.get("direction", "LONG")).upper().startswith(("S", "SHORT")) else Direction.LONG
    kind_raw = str(payload.get("kind", payload.get("action", "ENTRY"))).upper()
    if "EXIT" in kind_raw:
        kind = Kind.EXIT
    elif "MODIFY" in kind_raw or "UPDATE" in kind_raw:
        kind = Kind.MODIFY
    elif "ENTRY" in kind_raw or "NEW" in kind_raw or "BUY" in kind_raw or "SELL" in kind_raw:
        kind = Kind.ENTRY
    else:
        kind = Kind.NOTE
    opt_raw = payload.get("option_type") or payload.get("optionType")
    option_type = None
    if opt_raw and str(opt_raw).upper() in ("CE", "PE"):
        option_type = OptionType(str(opt_raw).upper())
    horizon_raw = str(payload.get("horizon", payload.get("duration", "SWING"))).upper()
    horizon = next((h for h in Horizon if h.value in horizon_raw), Horizon.SWING)
    inst = Instrument(
        underlying=str(payload.get("symbol") or payload.get("underlying") or payload.get("ticker") or "").upper().strip(),
        segment=segment,
        exchange=str(payload.get("exchange") or ("MCX" if segment == Segment.COMMODITY else "NSE")).upper(),
        direction=direction,
        kind="OPT" if (segment == Segment.OPTIONS or option_type) else ("FUT" if segment == Segment.FUTURES else "SPOT"),
        strike=_f(payload.get("strike")),
        option_type=option_type,
        expiry=payload.get("expiry"),
        lot_size=int(payload["lot_size"]) if payload.get("lot_size") else None,
    )
    entry_low = _f(payload.get("entry_low") if payload.get("entry_low") is not None else payload.get("entry"))
    entry_high = _f(payload.get("entry_high") if payload.get("entry_high") is not None else payload.get("entry"))
    sid = str(payload.get("id") or payload.get("trade_id") or uuid.uuid4().hex[:10])
    return Signal(
        source=source,
        source_id=sid,
        kind=kind,
        instrument=inst,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=_f(payload.get("stop_loss") or payload.get("sl")),
        targets=_targets(payload.get("targets") if payload.get("targets") is not None else payload.get("target")),
        horizon=horizon,
        posted_at=payload.get("posted_at") or payload.get("time"),
        raw_text=str(payload.get("raw_text") or payload.get("text") or ""),
        meta={k: v for k, v in payload.items() if k not in {"symbol", "underlying", "ticker", "raw_text", "text"}},
    )


# ---------------------------------------------------------------------------
# Text path: best-effort parser for the free-text push notifications / cards.
# ---------------------------------------------------------------------------

_HORIZON_WORDS = {
    "INTRADAY": Horizon.INTRADAY,
    "BTST": Horizon.INTRADAY,
    "SWING": Horizon.SWING,
    "POSITIONAL": Horizon.POSITIONAL,
    "WEEKLY": Horizon.WEEKLY,
}

_NUM = r"-?\d+(?:\.\d+)?"


def from_text(text: str, source: str = "manual", source_id: Optional[str] = None) -> Optional[Signal]:
    raw = text.strip()
    if not raw:
        return None
    upper = raw.upper()
    direction = Direction.SHORT if re.search(r"\b(SELL|SHORT|BEARISH)\b", upper) else Direction.LONG
    if re.search(r"\bBUY|LONG|BULLISH\b", upper):
        direction = Direction.LONG

    option_type = None
    if re.search(r"\bCE\b|\bCALL\b", upper):
        option_type = OptionType.CE
    elif re.search(r"\bPE\b|\bPUT\b", upper):
        option_type = OptionType.PE

    strike = None
    m = re.search(r"\b(\d{3,6})\s*(CE|PE|CALL|PUT)\b", upper)
    if m:
        strike = float(m.group(1))

    # symbol: first ALL-CAPS token that is not a keyword
    skip = {"BUY", "SELL", "SHORT", "LONG", "TGT", "TARGET", "SL", "STOP", "ENTRY", "AT", "CE", "PE",
            "CALL", "PUT", "FUT", "FUTURES", "OPTION", "OPT", "INTRADAY", "SWING", "POSITIONAL", "WEEKLY"}
    symbol = None
    for tok in re.findall(r"[A-Z][A-Z0-9&-]{2,}", upper):
        if tok not in skip:
            symbol = tok
            break
    if not symbol:
        return None

    if option_type:
        segment = Segment.OPTIONS
    elif symbol in {"GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL", "CRUDEOILM", "NATURALGAS", "COPPER", "ZINC"}:
        segment = Segment.COMMODITY
    else:
        segment = Segment.FUTURES

    sl = None
    m = re.search(rf"\b(?:SL|STOP(?:\s*LOSS)?)\b[:\s@]*({_NUM})", upper)
    if m:
        sl = float(m.group(1))

    targets = [float(x) for x in re.findall(rf"\b(?:TGT|TARGET|TP)\s*[:\s@]*({_NUM})", upper)]
    if not targets:
        m = re.search(rf"(?:TGT|TARGET|TP)[s]?[:\s@]*((?:{_NUM}[\s,/]*)+)", upper)
        if m:
            targets = [float(x) for x in re.findall(_NUM, m.group(1))]

    entries = [float(x) for x in re.findall(rf"(?:@|\b(?:ENTRY|ABOVE|AT)\b)\s*({_NUM})", upper)]
    if not entries:
        m = re.search(rf"({_NUM})\s*(?:-|–|to|TO)\s*({_NUM})", upper)
        if m:
            entries = [float(m.group(1)), float(m.group(2))]
    if not entries:
        head = re.split(r"\b(?:SL|STOP|TGT|TARGET)\b", upper)[0]
        nums = [float(x) for x in re.findall(_NUM, head)]
        nums = [n for n in nums if not (strike is not None and abs(n - strike) < 1e-9)]
        if nums:
            entries = [nums[-1]]
    entry_low = min(entries) if entries else None
    entry_high = max(entries) if entries else None

    horizon = Horizon.SWING
    for word, h in _HORIZON_WORDS.items():
        if word in upper:
            horizon = h
            break

    is_exit = bool(re.search(r"\b(EXIT|BOOK|SQUARE OFF|CLOSE|SL HIT|TARGET HIT)\b", upper))
    kind = Kind.EXIT if is_exit else Kind.ENTRY
    if re.search(r"\bHOLD\b|\bTRAIL\b", upper) and not is_exit:
        kind = Kind.MODIFY

    return Signal(
        source=source,
        source_id=source_id or uuid.uuid4().hex[:10],
        kind=kind,
        instrument=Instrument(
            underlying=symbol,
            segment=segment,
            exchange="MCX" if segment == Segment.COMMODITY else "NSE",
            direction=direction,
            kind="OPT" if segment == Segment.OPTIONS else "FUT",
            strike=strike,
            option_type=option_type,
        ),
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=sl,
        targets=targets,
        horizon=horizon,
        raw_text=raw,
    )
