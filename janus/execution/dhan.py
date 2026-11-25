from __future__ import annotations

import csv
import io
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Dict, Optional

from ..models import Direction, Instrument, OptionType, Order, Segment

log = logging.getLogger("janus.dhan")

BASE = "https://api.dhan.co/v2"
SCRIP_MASTER = f"{BASE}/instrument"

SEGMENT_MAP = {
    (Segment.FUTURES, "NSE"): "NSE_FO",
    (Segment.OPTIONS, "NSE"): "NSE_FO",
    (Segment.FUTURES, "BSE"): "BSE_FO",
    (Segment.OPTIONS, "BSE"): "BSE_FO",
    (Segment.COMMODITY, "MCX"): "MCX_COMM",
    (Segment.EQUITY, "NSE"): "NSE_EQ",
    (Segment.EQUITY, "BSE"): "BSE_EQ",
}

PRODUCT_MAP = {
    Segment.FUTURES: "MARGIN",
    Segment.OPTIONS: "MARGIN",
    Segment.COMMODITY: "MARGIN",
    Segment.EQUITY: "CNC",
}


class DhanBroker:
    """Minimal, from-scratch Dhan v2 REST client.

    Live order placement is intentionally gated behind JANUS_LIVE=1 *and*
    config mode=live. Verify payload fields against Dhan's current API docs
    before enabling live.
    """

    name = "dhan"

    def __init__(self, client_id: str, access_token: str, scrip_master_path: str = "data/dhan_scripmaster.csv"):
        self.client_id = client_id
        self.access_token = access_token
        self.scrip_master_path = scrip_master_path
        self._master: Optional[list] = None

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id,
        }

    def validate(self) -> bool:
        try:
            req = urllib.request.Request(f"{BASE}/profile", headers=self._headers())
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status == 200
        except Exception as e:
            log.error("Dhan profile check failed: %s", e)
            return False

    def _load_master(self) -> list:
        if self._master is not None:
            return self._master
        if os.path.exists(self.scrip_master_path):
            with open(self.scrip_master_path, newline="") as fh:
                self._master = list(csv.DictReader(fh))
            return self._master
        req = urllib.request.Request(SCRIP_MASTER, headers=self._headers())
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode()
        self._master = list(csv.DictReader(io.StringIO(text)))
        os.makedirs(os.path.dirname(self.scrip_master_path), exist_ok=True)
        with open(self.scrip_master_path, "w", newline="") as fh:
            fh.write(text)
        return self._master

    def resolve(self, inst: Instrument) -> Optional[dict]:
        wanted_seg = SEGMENT_MAP.get((inst.segment, inst.exchange), "NSE_FO")
        rows = [r for r in self._load_master() if (r.get("SEM_EXM_TYP") or "").upper().startswith(
            "OPT" if inst.kind == "OPT" else "FUT" if inst.kind == "FUT" else "EQ")]
        underlying = inst.underlying.upper()
        out = []
        for r in rows:
            tsym = (r.get("SEM_TRADING_SYMBOL") or "").upper()
            if not tsym.startswith(underlying):
                continue
            if inst.expiry and str(r.get("SEM_EXPIRY_DATE", ""))[:10] != str(inst.expiry)[:10]:
                continue
            if inst.strike and inst.option_type:
                if abs(float(r.get("SEM_STRIKE_PRICE") or 0) - float(inst.strike)) > 0.01:
                    continue
                if (r.get("SEM_OPTION_TYPE") or "").upper() != inst.option_type.value:
                    continue
            out.append(r)
        if not out:
            return None
        out.sort(key=lambda r: r.get("SEM_EXPIRY_DATE", ""))
        best = out[0]
        return {
            "security_id": best.get("SEM_SMST_SEC_ID"),
            "trading_symbol": best.get("SEM_TRADING_SYMBOL"),
            "lot_size": int(best.get("SEM_LOT_SIZE") or 1),
            "expiry": best.get("SEM_EXPIRY_DATE"),
            "exchange_segment": wanted_seg,
        }

    def place_order(self, inst: Instrument, direction: Direction, qty: float,
                    limit: Optional[float] = None, stop: bool = False, trigger: Optional[float] = None) -> Order:
        o = Order(order_id=Order.new_id(), position_id="", instrument=inst,
                  side="BUY" if direction == Direction.LONG else "SELL", qty=qty,
                  order_type="LIMIT" if limit else "MARKET", price=limit, trigger_price=trigger)
        if os.environ.get("JANUS_LIVE") != "1":
            o.status = "REJECTED"
            o.message = "live trading disabled (JANUS_LIVE != 1)"
            return o
        contract = self.resolve(inst)
        if not contract:
            o.status = "REJECTED"
            o.message = "instrument not resolvable in Dhan scrip master"
            return o
        lots = max(1, round(qty / contract["lot_size"]))
        payload = {
            "dhanClientId": self.client_id,
            "transactionType": o.side,
            "exchangeSegment": contract["exchange_segment"],
            "productType": PRODUCT_MAP.get(inst.segment, "MARGIN"),
            "orderType": "SL" if stop else ("LIMIT" if limit else "MARKET"),
            "validity": "DAY",
            "securityId": contract["security_id"],
            "quantity": lots * contract["lot_size"],
            "price": limit or 0,
            "triggerPrice": trigger or 0,
            "disclosedQuantity": 0,
            "afterMarketOrder": False,
        }
        try:
            req = urllib.request.Request(f"{BASE}/orders", data=json.dumps(payload).encode(),
                                         method="POST", headers=self._headers())
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode())
            o.broker_order_id = body.get("orderId")
            o.status = "PENDING"
            o.message = "submitted"
        except urllib.error.HTTPError as e:
            o.status = "REJECTED"
            o.message = f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            o.status = "REJECTED"
            o.message = str(e)
        return o
