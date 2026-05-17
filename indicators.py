"""Pure pandas/numpy indicator functions. No I/O.

All functions take pd.Series or pd.DataFrame inputs and return same-length
output, with NaNs in the warmup period.
"""
import numpy as np
import pandas as pd


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    line = ema_fast - ema_slow
    sig = ema(line, signal)
    return line, sig, line - sig


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP — resets daily (UTC date)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    idx = df.index
    if hasattr(idx, "date"):
        day = pd.Series(idx.date, index=idx)
    else:  # MultiIndex (symbol, timestamp)
        day = pd.Series(idx.get_level_values(-1).date, index=idx)
    cum_pv = pv.groupby(day.values).cumsum()
    cum_v = df["volume"].groupby(day.values).cumsum().replace(0, np.nan)
    return (cum_pv / cum_v).reindex(df.index)


def bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = sma(close, period)
    std = close.rolling(period).std()
    return mid - std_mult * std, mid, mid + std_mult * std


def true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    return pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — trend strength (0-100)."""
    h, l = df["high"], df["low"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)).astype(float) * up.clip(lower=0)
    minus_dm = ((dn > up) & (dn > 0)).astype(float) * dn.clip(lower=0)
    atr_v = atr(df, period).replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_v
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_v
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()
