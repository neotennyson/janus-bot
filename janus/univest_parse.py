from __future__ import annotations

import re
from typing import Iterable, List, Optional

from .models import Direction, Horizon, Instrument, Kind, OptionType, Segment, Signal

_SYM_KEYS = ("symbol", "tradingsymbol", "trading_symbol", "scrip", "underlying", "instrument", "stockname", "stock", "name")
_ENTRY_KEYS = ("entryprice", "entry_price", "entryrange", "entry_rate", "entry", "buyprice", "buy_price", "price")
_SL_KEYS = ("stoploss", "stop_loss", "stoplosspre", "slprice", "sl_price", "sl")
_TGT_KEYS = ("targets", "targetprice", "target_price", "target1", "target", "tgt")
_DIR_KEYS = ("direction", "side", "action", "signal", "transactiontype", "ordertype", "call")
_SEG_KEYS = ("segment", "assetclass", "asset_class", "asset", "category", "product")
_STRIKE_KEYS = ("strikeprice", "strike_price", "strike")
_OPT_KEYS = ("optiontype", "option_type", "option", "instrumenttype", "instrument_type")
_EXP_KEYS = ("expirydate", "expiry_date", "expiry")
_HORIZON_KEYS = ("horizon", "duration", "tradetype", "trade_type", "term")

_INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
_COMMODITIES = {"GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL", "CRUDEOILM", "NATURALGAS",
                "COPPER", "ZINC", "ALUMINIUM", "LEAD", "NICKEL"}


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _lookup(d: dict, keys) -> Optional[object]:
    normed = {}
    for k, v in d.items():
        normed.setdefault(_norm(k), v)
    for want in keys:
        if want in normed and normed[want] not in (None, "", [], {}):
            return normed[want]
    return None


def _num(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def _nums(v) -> List[float]:
    if v is None:
        return []
    if isinstance(v, (int, float)):
        return [float(v)]
    if isinstance(v, list):
        out = []
        for x in v:
            n = _num(x)
            if n is not None:
                out.append(n)
        return out
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))]


def _direction(v) -> Direction:
    s = str(v or "").upper()
    if re.search(r"\b(SELL|SHORT|PE|PUT|BEAR)\b", s):
        return Direction.SHORT
    return Direction.LONG


def _segment(d: dict, symbol: str, option_type: Optional[str], strike: Optional[float], exchange: str) -> Segment:
    seg = str(_lookup(d, _SEG_KEYS) or "").upper()
    if option_type or strike is not None or "OPT" in seg:
        return Segment.OPTIONS
    if symbol in _COMMODITIES or exchange == "MCX" or "COMMOD" in seg:
        return Segment.COMMODITY
    if "EQUIT" in seg or "CASH" in seg:
        return Segment.EQUITY
    return Segment.FUTURES


def _horizon(v) -> Horizon:
    s = str(v or "").upper()
    for h in Horizon:
        if h.value in s:
            return h
    if "BTST" in s or "INTRA" in s:
        return Horizon.INTRADAY
    if "POSITION" in s:
        return Horizon.POSITIONAL
    if "WEEK" in s:
        return Horizon.WEEKLY
    return Horizon.SWING


def _record_to_signal(d: dict, source: str) -> Optional[Signal]:
    symbol_raw = _lookup(d, _SYM_KEYS)
    if symbol_raw is None:
        return None
    symbol = re.sub(r"[^A-Z0-9&-]", "", str(symbol_raw).upper())
    if not symbol or symbol.isdigit() or symbol in {"BUY", "SELL"}:
        return None

    entry = _num(_lookup(d, _ENTRY_KEYS))
    sl = _num(_lookup(d, _SL_KEYS))
    targets = _nums(_lookup(d, _TGT_KEYS))
    strike = _num(_lookup(d, _STRIKE_KEYS))
    opt_raw = str(_lookup(d, _OPT_KEYS) or "").upper()
    option_type = OptionType.CE if "CE" in opt_raw or "CALL" in opt_raw else (
        OptionType.PE if "PE" in opt_raw or "PUT" in opt_raw else None)
    expiry = _lookup(d, _EXP_KEYS)
    direction = _direction(_lookup(d, _DIR_KEYS))

    # An option may hide CE/PE in the trading symbol rather than a field.
    if option_type is None:
        m = re.search(r"(\d{3,6})\s*(CE|PE)", symbol)
        if m:
            strike = strike or float(m.group(1))
            option_type = OptionType(m.group(2))
    if option_type is None and re.search(r"\b(CE|PE)\b", str(_lookup(d, _SYM_KEYS) or "").upper()):
        option_type = OptionType.CE if "CE" in str(symbol_raw).upper() else OptionType.PE

    exchange = "MCX" if symbol in _COMMODITIES else "NSE"
    segment = _segment(d, symbol, option_type, strike, exchange)

    kind = Kind.NOTE
    text = " ".join(str(v) for v in d.values() if isinstance(v, str)).upper()
    if re.search(r"\bEXIT\b|\bCLOSE\b|BOOK", text):
        kind = Kind.EXIT
    elif entry is not None or sl is not None or targets:
        kind = Kind.ENTRY

    if kind == Kind.ENTRY and entry is None:
        return None
    if kind == Kind.ENTRY and sl is None:
        return None

    sid = str(_lookup(d, ("id", "tradeid", "trade_id", "recommendationid", "recommendation_id", "ideaId")) or
              f"{symbol}-{entry}-{sl}")

    return Signal(
        source=source,
        source_id=sid,
        kind=kind,
        instrument=Instrument(
            underlying=symbol,
            segment=segment,
            exchange=exchange,
            direction=direction,
            kind="OPT" if segment == Segment.OPTIONS else "FUT",
            strike=strike,
            option_type=option_type,
            expiry=str(expiry)[:10] if expiry else None,
        ),
        entry_low=entry,
        entry_high=entry,
        stop_loss=sl,
        targets=targets,
        horizon=_horizon(_lookup(d, _HORIZON_KEYS)),
        raw_text=str(d),
    )


def signals_from_payload(obj, source: str = "univest") -> List[Signal]:
    """Walk any JSON structure and pull out every trade idea it contains."""
    out: List[Signal] = []
    seen = set()

    def walk(node):
        if isinstance(node, dict):
            sig = _record_to_signal(node, source)
            if sig and sig.key not in seen:
                seen.add(sig.key)
                out.append(sig)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obj)
    return out
