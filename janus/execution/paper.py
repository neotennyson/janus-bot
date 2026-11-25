from __future__ import annotations

from ..models import Direction, Instrument, Order


class PaperBroker:
    """Deterministic local simulator. Fills a LIMIT entry only if the market
    price is at or better than the limit; exits fill at the market price."""

    name = "paper"

    def place_entry(self, position_id: str, instrument: Instrument, direction: Direction,
                    qty: float, limit: float, market_price: float) -> Order:
        o = Order(order_id=Order.new_id(), position_id=position_id, instrument=instrument,
                  side="BUY" if direction == Direction.LONG else "SELL", qty=qty,
                  order_type="LIMIT", price=limit)
        can_fill = (market_price <= limit) if direction == Direction.LONG else (market_price >= limit)
        if can_fill:
            fill = min(limit, market_price) if direction == Direction.LONG else max(limit, market_price)
            o.status = "FILLED"
            o.price = fill
            o.message = f"filled @ {fill:.4f}"
        else:
            o.status = "CANCELLED"
            o.message = f"not fillable (market {market_price:.4f} vs limit {limit:.4f})"
        return o

    def place_exit(self, position_id: str, instrument: Instrument, direction: Direction,
                   qty: float, market_price: float, reason: str = "") -> Order:
        o = Order(order_id=Order.new_id(), position_id=position_id, instrument=instrument,
                  side="SELL" if direction == Direction.LONG else "BUY", qty=qty,
                  order_type="MARKET", price=market_price)
        o.status = "FILLED"
        o.message = f"exit @ {market_price:.4f} ({reason})"
        return o
