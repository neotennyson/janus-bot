from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

import pandas as pd

from .models import Instrument, Segment

INDEX_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "^NSEFIN",
    "MIDCPNIFTY": "^NSEMID",
    "NIFTYNXT50": "^NSENXT50",
    "SENSEX": "^BSESN",
    "BANKEX": "^BSEBANK",
}

# MCX commodities are not on yfinance directly; these are the closest global
# proxies used for DIRECTION/TREND analysis only (not for contract pricing).
COMMODITY_MAP = {
    "GOLD": "GC=F",
    "GOLDM": "GC=F",
    "SILVER": "SI=F",
    "SILVERM": "SI=F",
    "CRUDEOIL": "CL=F",
    "CRUDEOILM": "CL=F",
    "NATURALGAS": "NG=F",
    "COPPER": "HG=F",
    "ZINC": "ZN=F",
    "ALUMINIUM": "ALI=F",
    "LEAD": "PB=F",
    "NICKEL": "NI=F",
}


def analysis_symbol(inst: Instrument) -> str:
    und = inst.underlying.upper().strip()
    if inst.segment == Segment.COMMODITY:
        return COMMODITY_MAP.get(und, und)
    if und in INDEX_MAP:
        return INDEX_MAP[und]
    return f"{und}.NS"


class DataFeed:
    def __init__(self, cache_dir: str = "data/cache", provider: str = "yfinance", lookback_days: int = 400):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.lookback_days = lookback_days

    def _cache_path(self, symbol: str) -> Path:
        safe = symbol.replace("^", "_").replace("=", "_").replace("/", "_")
        return self.cache_dir / f"{safe}.csv"

    def history(self, symbol: str, days: Optional[int] = None) -> pd.DataFrame:
        days = days or self.lookback_days
        end = dt.date.today()
        start = end - dt.timedelta(days=days)
        cache = self._cache_path(symbol)
        if cache.exists():
            try:
                df = pd.read_csv(cache, parse_dates=["Date"], index_col="Date")
                if not df.empty and (end - df.index[-1].date()).days <= 4:
                    return df
            except Exception:
                pass
        try:
            import yfinance as yf

            df = yf.download(symbol, start=start.isoformat(), end=(end + dt.timedelta(days=1)).isoformat(),
                             progress=False, auto_adjust=False)
            if df is None or df.empty:
                return pd.DataFrame()
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            df.to_csv(cache)
            return df
        except Exception:
            if cache.exists():
                return pd.read_csv(cache, parse_dates=["Date"], index_col="Date")
            return pd.DataFrame()
