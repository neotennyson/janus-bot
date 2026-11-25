from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = atr(df, period) * period
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr.replace(0.0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr.replace(0.0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().fillna(0.0)


def bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = sma(series, window)
    std = series.rolling(window, min_periods=window).std()
    return mid + num_std * std, mid, mid - num_std * std


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].replace(0, np.nan)
    return (typical * vol).cumsum() / vol.cumsum()


def snapshot(df: pd.DataFrame) -> dict:
    close = df["Close"]
    last = float(close.iloc[-1])
    out = {
        "price": last,
        "sma20": float(sma(close, 20).iloc[-1]),
        "sma50": float(sma(close, 50).iloc[-1]),
        "sma200": float(sma(close, 200).iloc[-1]) if len(close) >= 200 else float("nan"),
        "ema20": float(ema(close, 20).iloc[-1]),
        "rsi14": float(rsi(close, 14).iloc[-1]),
        "atr14": float(atr(df, 14).iloc[-1]),
        "adx14": float(adx(df, 14).iloc[-1]),
    }
    macd_line, signal_line, hist = macd(close)
    out["macd_hist"] = float(hist.iloc[-1])
    out["macd_line"] = float(macd_line.iloc[-1])
    out["macd_signal"] = float(signal_line.iloc[-1])
    out["vol_rel"] = float(df["Volume"].iloc[-1] / max(df["Volume"].rolling(20).mean().iloc[-1], 1e-9))
    if len(df) >= 20:
        out["day_high"] = float(df["High"].iloc[-1])
        out["day_low"] = float(df["Low"].iloc[-1])
        out["range_high_20"] = float(df["High"].rolling(20).max().iloc[-1])
        out["range_low_20"] = float(df["Low"].rolling(20).min().iloc[-1])
    return out
