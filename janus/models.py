from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Segment(str, Enum):
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"
    COMMODITY = "COMMODITY"
    EQUITY = "EQUITY"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Kind(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    MODIFY = "MODIFY"
    NOTE = "NOTE"


class Horizon(str, Enum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"
    POSITIONAL = "POSITIONAL"
    WEEKLY = "WEEKLY"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


def _plain(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


@dataclass
class Instrument:
    underlying: str
    segment: Segment
    exchange: str = "NSE"
    direction: Direction = Direction.LONG
    kind: str = "FUT"
    strike: Optional[float] = None
    option_type: Optional[OptionType] = None
    expiry: Optional[str] = None
    lot_size: Optional[int] = None

    def to_dict(self) -> dict:
        return _plain(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "Instrument":
        d = dict(d)
        d["segment"] = Segment(d.get("segment", "FUTURES"))
        d["direction"] = Direction(d.get("direction", "LONG"))
        if d.get("option_type"):
            d["option_type"] = OptionType(d["option_type"])
        return cls(**d)


@dataclass
class Signal:
    source: str
    source_id: str
    kind: Kind
    instrument: Instrument
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    targets: List[float] = field(default_factory=list)
    horizon: Horizon = Horizon.SWING
    posted_at: Optional[str] = None
    received_at: str = field(default_factory=now_utc)
    raw_text: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def entry_ref(self) -> Optional[float]:
        if self.entry_low is not None and self.entry_high is not None:
            return (self.entry_low + self.entry_high) / 2.0
        return self.entry_low if self.entry_low is not None else self.entry_high

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> dict:
        d = _plain(self.__dict__)
        d["instrument"] = self.instrument.to_dict()
        d["entry_ref"] = self.entry_ref
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Signal":
        d = dict(d)
        d["kind"] = Kind(d.get("kind", "ENTRY"))
        d["horizon"] = Horizon(d.get("horizon", "SWING"))
        d["instrument"] = Instrument.from_dict(d["instrument"])
        d.pop("entry_ref", None)
        d.pop("key", None)
        return cls(**d)


@dataclass
class Decision:
    signal_key: str
    action: str                    # ENTER | SKIP | EXIT | HOLD | MODIFY
    score: float
    passed: bool
    reasons: List[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    size: Optional[float] = None
    entry_limit: Optional[float] = None
    sl: Optional[float] = None
    target: Optional[float] = None
    rr: Optional[float] = None
    created_at: str = field(default_factory=now_utc)

    def to_dict(self) -> dict:
        return _plain(self.__dict__)


@dataclass
class Position:
    position_id: str
    signal_key: str
    instrument: Instrument
    direction: Direction
    qty: float
    entry_price: float
    stop_loss: float
    target: float
    opened_at: str = field(default_factory=now_utc)
    status: str = "OPEN"           # OPEN | CLOSED
    exit_price: Optional[float] = None
    closed_at: Optional[str] = None
    realized_pnl: float = 0.0
    thesis: dict = field(default_factory=dict)
    peak_r: float = 0.0
    partial_done: bool = False
    stop_moved_to_be: bool = False

    @staticmethod
    def new_id() -> str:
        return "pos_" + uuid.uuid4().hex[:12]

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    def r_multiple(self, price: float) -> float:
        risk = self.risk_per_unit
        if risk <= 0:
            return 0.0
        if self.direction == Direction.LONG:
            return (price - self.entry_price) / risk
        return (self.entry_price - price) / risk

    def to_dict(self) -> dict:
        d = _plain(self.__dict__)
        d["instrument"] = self.instrument.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        d = dict(d)
        d["instrument"] = Instrument.from_dict(d["instrument"])
        d["direction"] = Direction(d["direction"])
        return cls(**d)


@dataclass
class Order:
    order_id: str
    position_id: str
    instrument: Instrument
    side: str                      # BUY | SELL
    qty: float
    order_type: str = "LIMIT"
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    status: str = "PENDING"        # PENDING | FILLED | REJECTED | CANCELLED
    broker_order_id: Optional[str] = None
    message: str = ""
    created_at: str = field(default_factory=now_utc)

    @staticmethod
    def new_id() -> str:
        return "ord_" + uuid.uuid4().hex[:12]

    def to_dict(self) -> dict:
        d = _plain(self.__dict__)
        d["instrument"] = self.instrument.to_dict()
        return d
