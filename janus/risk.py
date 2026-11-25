from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from .models import Position


@dataclass
class RiskVerdict:
    allowed: bool
    qty: float = 0.0
    lots: Optional[int] = None
    risk_amount: float = 0.0
    reason: str = ""


def size_position(capital: float, per_trade_pct: float, entry: float, sl: float,
                  lot_size: Optional[int] = None, price: Optional[float] = None) -> RiskVerdict:
    fill_px = price or entry
    risk_per_unit = abs(fill_px - sl)
    if risk_per_unit <= 0:
        return RiskVerdict(False, reason="zero risk distance")
    risk_budget = capital * per_trade_pct
    units = risk_budget / risk_per_unit
    if lot_size and lot_size > 0:
        lots = int(units // lot_size)
        if lots < 1:
            return RiskVerdict(False, reason=f"risk budget buys <1 lot ({lot_size} units/lot)")
        qty = lots * lot_size
        return RiskVerdict(True, qty=float(qty), lots=lots, risk_amount=abs(fill_px - sl) * qty,
                           reason=f"{lots} lot(s)")
    qty = float(int(units))
    if qty < 1:
        return RiskVerdict(False, reason="risk budget buys <1 unit")
    return RiskVerdict(True, qty=qty, risk_amount=risk_per_unit * qty, reason="ok")


def portfolio_guard(open_positions: List[Position], cfg, instrument, realized_today: float, capital: float) -> RiskVerdict:
    max_open = int(cfg.get("risk.max_open_positions", 6))
    if len(open_positions) >= max_open:
        return RiskVerdict(False, reason=f"max open positions {max_open} reached")

    per_und = int(cfg.get("risk.max_positions_per_underlying", 1))
    same = [p for p in open_positions if p.instrument.underlying == instrument.underlying]
    if len(same) >= per_und:
        return RiskVerdict(False, reason=f"already positioned in {instrument.underlying}")

    daily_limit = capital * float(cfg.get("risk.daily_loss_limit_pct", 0.02))
    if realized_today <= -daily_limit:
        return RiskVerdict(False, reason=f"daily loss limit hit ({realized_today:.0f})")

    return RiskVerdict(True, reason="ok")


def drawdown_breaker(equity: float, peak_equity: float, cfg) -> bool:
    if peak_equity <= 0:
        return False
    dd = (peak_equity - equity) / peak_equity
    return dd >= float(cfg.get("risk.max_drawdown_pct", 0.20))
