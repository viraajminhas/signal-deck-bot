"""Three independent signals + ensemble vote.

Each signal returns dict with at least:
  - 'score': float in [-1, +1]  (negative = short, positive = long, 0 = flat)
  - other fields = debug context for the SQLite log

Ensemble combines them by weighted vote, with a disagreement penalty.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from config import CFG
from indicators import macd, rsi, session_vwap, sma, bollinger, atr, adx


def _empty(reason: str) -> dict:
    return {"score": 0.0, "reason": reason}


# ============================================================================
# Signal 1: Multifactor (SIGNAL DECK style)
#   MACD + RSI + VWAP + Volume confirmation
# ============================================================================
def multifactor_signal(df: pd.DataFrame) -> dict:
    if len(df) < 50:
        return _empty(f"only {len(df)} bars")

    close = df["close"]
    line, sig, hist = macd(close)
    r = rsi(close)
    vw = session_vwap(df)
    vol_avg = df["volume"].rolling(20).mean()

    i = -1
    px = float(close.iloc[i])

    # MACD vote: line vs signal, plus histogram direction
    macd_vote = 0.0
    if line.iloc[i] > sig.iloc[i]:
        macd_vote = 1.0 if hist.iloc[i] > hist.iloc[i - 1] else 0.5
    elif line.iloc[i] < sig.iloc[i]:
        macd_vote = -1.0 if hist.iloc[i] < hist.iloc[i - 1] else -0.5

    # RSI vote — momentum confirmation, not extremes
    rsi_v = float(r.iloc[i]) if not pd.isna(r.iloc[i]) else 50.0
    if rsi_v > 60:
        rsi_vote = 1.0
    elif rsi_v < 40:
        rsi_vote = -1.0
    else:
        rsi_vote = 0.0

    # VWAP vote — intraday bias
    vwp = float(vw.iloc[i]) if not pd.isna(vw.iloc[i]) else px
    if px > vwp * 1.001:
        vwap_vote = 1.0
    elif px < vwp * 0.999:
        vwap_vote = -1.0
    else:
        vwap_vote = 0.0

    # Volume confirmation — discount signal if volume is below average
    vol_now = float(df["volume"].iloc[i])
    vol_mean = float(vol_avg.iloc[i]) if not pd.isna(vol_avg.iloc[i]) else vol_now
    vol_ok = vol_now > vol_mean

    raw = (macd_vote + rsi_vote + vwap_vote) / 3.0
    score = raw if vol_ok else raw * 0.6  # weak-volume signals get faded

    return {
        "score": float(np.clip(score, -1, 1)),
        "macd_vote": macd_vote,
        "rsi": rsi_v,
        "px_vs_vwap": float(px - vwp),
        "vol_ratio": float(vol_now / vol_mean) if vol_mean > 0 else None,
        "vol_ok": vol_ok,
    }


# ============================================================================
# Signal 2: Mean Reversion
#   Buy oversold (RSI<30 + price below lower Bollinger), sell overbought.
#   Suppressed in strong trends (ADX > 30).
# ============================================================================
def mean_rev_signal(df: pd.DataFrame) -> dict:
    if len(df) < 50:
        return _empty(f"only {len(df)} bars")

    close = df["close"]
    r = rsi(close)
    lower, mid, upper = bollinger(close)
    a = adx(df)

    i = -1
    px = float(close.iloc[i])
    rsi_v = float(r.iloc[i]) if not pd.isna(r.iloc[i]) else 50.0
    adx_v = float(a.iloc[i]) if not pd.isna(a.iloc[i]) else 0.0

    upper_v = float(upper.iloc[i]) if not pd.isna(upper.iloc[i]) else px
    mid_v = float(mid.iloc[i]) if not pd.isna(mid.iloc[i]) else px
    lower_v = float(lower.iloc[i]) if not pd.isna(lower.iloc[i]) else px
    band_width = max(upper_v - mid_v, 1e-9)
    bb_z = (px - mid_v) / band_width  # -1 = lower band, +1 = upper band

    # Suppress mean reversion in strong trends — it'll get rolled over
    if adx_v > 30:
        return {"score": 0.0, "rsi": rsi_v, "bb_z": bb_z, "adx": adx_v, "reason": "too trendy"}

    score = 0.0
    if rsi_v < 30 and bb_z < -0.8:
        score = min(1.0, (30 - rsi_v) / 15 + max(0.0, -bb_z - 0.8))
    elif rsi_v > 70 and bb_z > 0.8:
        score = -min(1.0, (rsi_v - 70) / 15 + max(0.0, bb_z - 0.8))

    return {
        "score": float(np.clip(score, -1, 1)),
        "rsi": rsi_v,
        "bb_z": bb_z,
        "adx": adx_v,
    }


# ============================================================================
# Signal 3: Trend Following
#   SMA(9) / SMA(21) / SMA(50) stack + ADX > 20 confirmation
# ============================================================================
def trend_signal(df: pd.DataFrame) -> dict:
    if len(df) < 60:
        return _empty(f"only {len(df)} bars")

    close = df["close"]
    s9 = sma(close, 9)
    s21 = sma(close, 21)
    s50 = sma(close, 50)
    a = adx(df)

    i = -1
    fast = float(s9.iloc[i])
    slow = float(s21.iloc[i])
    long_ma = float(s50.iloc[i]) if not pd.isna(s50.iloc[i]) else float(close.iloc[i])
    px = float(close.iloc[i])
    adx_v = float(a.iloc[i]) if not pd.isna(a.iloc[i]) else 0.0

    if adx_v < 20:
        return {"score": 0.0, "sma9": fast, "sma21": slow, "adx": adx_v, "reason": "no trend"}

    score = 0.0
    if fast > slow > long_ma and px > long_ma:
        score = min(1.0, (adx_v - 20) / 30)
    elif fast < slow < long_ma and px < long_ma:
        score = -min(1.0, (adx_v - 20) / 30)

    return {
        "score": float(np.clip(score, -1, 1)),
        "sma9": fast,
        "sma21": slow,
        "sma50": long_ma,
        "adx": adx_v,
    }


# ============================================================================
# Ensemble: weighted vote with disagreement penalty
# ============================================================================
def ensemble(mf: dict, mr: dict, tr: dict) -> dict:
    w = CFG.SIGNAL_WEIGHTS
    raw = (
        mf["score"] * w["multifactor"]
        + mr["score"] * w["mean_rev"]
        + tr["score"] * w["trend"]
    )

    # Disagreement penalty — if two of three signals point opposite ways, fade by half
    active = [s["score"] for s in (mf, mr, tr) if abs(s["score"]) > 0.1]
    if len(active) >= 2:
        pos = sum(1 for s in active if s > 0)
        neg = sum(1 for s in active if s < 0)
        if pos and neg:
            raw *= 0.5

    if raw > CFG.MIN_ENSEMBLE_SCORE:
        decision = "long"
    elif raw < -CFG.MIN_ENSEMBLE_SCORE:
        decision = "short"
    else:
        decision = "hold"

    return {"score": float(raw), "decision": decision}
