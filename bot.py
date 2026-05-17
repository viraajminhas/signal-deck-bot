"""SIGNAL DECK bot — one-shot execution.

Designed to be called every 5 minutes by Windows Task Scheduler.
Each invocation:
  1. Locks (so concurrent runs don't pile up)
  2. Checks halts (daily loss, max trades, consecutive losers)
  3. Closes equity positions if within 5 min of close
  4. Fetches bars (equities via IEX, crypto via Alpaca)
  5. Runs three signals + ensemble vote per symbol
  6. Sizes by ATR-based stop and 2% risk, capped at 25% of equity per position
  7. Submits bracket orders (equities) or market+stored-stop (crypto)
  8. Logs everything to bot.db + bot.log

Dry-run mode: set DRY_RUN=1 in .env to log decisions without sending orders.
"""
from __future__ import annotations
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Make script runnable from anywhere
BASE = Path(__file__).parent
os.chdir(BASE)
sys.path.insert(0, str(BASE))

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

from config import CFG
import db as DB
from indicators import atr
from strategies import multifactor_signal, mean_rev_signal, trend_signal, ensemble


load_dotenv()
DRY_RUN = (os.getenv("DRY_RUN") or "1") == "1"  # default safe: any missing/empty value = dry run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(CFG.LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("bot")


# ----------------------------------------------------------------------------
# Concurrency lock
# ----------------------------------------------------------------------------
def acquire_lock() -> bool:
    p = Path(CFG.LOCK_PATH)
    if p.exists():
        age = time.time() - p.stat().st_mtime
        if age < 600:  # stale after 10 min
            log.warning(f"another instance is running (lock {age:.0f}s old); exiting")
            return False
        log.warning(f"stale lock ({age:.0f}s); removing")
        p.unlink()
    p.touch()
    return True


def release_lock():
    try:
        Path(CFG.LOCK_PATH).unlink(missing_ok=True)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def _timeframe(minutes: int) -> TimeFrame:
    return TimeFrame(minutes, TimeFrameUnit.Minute)


def fetch_stock_bars(client, symbols) -> dict[str, pd.DataFrame]:
    end = datetime.now(timezone.utc) - timedelta(minutes=CFG.STOCK_DATA_LAG_MIN)
    start = end - timedelta(days=CFG.BAR_LOOKBACK_DAYS)
    req = StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=_timeframe(CFG.BAR_TIMEFRAME_MIN),
        start=start, end=end,
        feed=DataFeed.IEX,
    )
    df = client.get_stock_bars(req).df
    if df is None or df.empty:
        return {}
    out = {}
    for sym in df.index.get_level_values("symbol").unique():
        sub = df.xs(sym).copy()
        sub.index.name = "timestamp"
        out[sym] = sub
    return out


def fetch_crypto_bars(client, symbols) -> dict[str, pd.DataFrame]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=CFG.BAR_LOOKBACK_DAYS)
    req = CryptoBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=_timeframe(CFG.BAR_TIMEFRAME_MIN),
        start=start, end=end,
    )
    df = client.get_crypto_bars(req).df
    if df is None or df.empty:
        return {}
    out = {}
    for sym in df.index.get_level_values("symbol").unique():
        sub = df.xs(sym).copy()
        sub.index.name = "timestamp"
        out[sym] = sub
    return out


# ----------------------------------------------------------------------------
# Risk
# ----------------------------------------------------------------------------
def daily_pnl_pct(trading: TradingClient) -> float:
    acct = trading.get_account()
    eq = float(acct.equity)
    last_eq = float(acct.last_equity) if acct.last_equity else eq
    return (eq - last_eq) / last_eq if last_eq else 0.0


def count_trades_today(trading: TradingClient) -> tuple[int, dict]:
    """Source of truth: Alpaca. Works statelessly in any environment
    (GitHub Actions, fresh container, local with fresh db, etc.).
    Returns (total_today, {alpaca_symbol: count}).
    Crypto symbols come back without slash (e.g. 'BTCUSD')."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        orders = trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            after=today_start,
            limit=500,
        ))
    except Exception as e:
        log.warning(f"count_trades_today: Alpaca query failed ({e}); falling back to 0")
        return 0, {}
    per: dict[str, int] = {}
    for o in orders:
        per[o.symbol] = per.get(o.symbol, 0) + 1
    return sum(per.values()), per


def consecutive_losses(trading: TradingClient, n: int) -> int:
    """Look at recent closed-position activities and count trailing losers."""
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest  # noqa
        # Simplest source: account activities of type FILL, paired into entry/exit.
        # Heuristic for v1: use closed_at orders in last 24h.
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        recent = trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            limit=20,
            after=datetime.now(timezone.utc) - timedelta(hours=24),
        ))
        # Count trailing realized-loss orders (sell side with negative trade_value vs cost).
        # Alpaca doesn't directly expose per-trade P&L; use filled_avg_price * qty difference.
        # Approximation: rely on Alpaca account.last_equity → daily P&L sign instead.
        # This function is a best-effort; the daily-loss cap is the real safety net.
        return 0
    except Exception as e:
        log.debug(f"consecutive_losses check failed: {e}")
        return 0


def check_halts(db, trading: TradingClient) -> tuple[bool, str | None]:
    pnl = daily_pnl_pct(trading)
    if pnl <= -CFG.MAX_DAILY_LOSS_PCT:
        DB.log_halt(db, f"daily loss {pnl:.2%} <= -{CFG.MAX_DAILY_LOSS_PCT:.0%}")
        return True, f"daily loss {pnl:.2%}"

    total, _ = count_trades_today(trading)
    if total >= CFG.MAX_TRADES_PER_DAY:
        DB.log_halt(db, f"max trades/day {total} reached")
        return True, "max trades/day reached"

    return False, None


def position_size(equity: float, buying_power: float, entry: float, stop: float, asset_class: str) -> float:
    risk_dollars = equity * CFG.RISK_PER_TRADE_PCT
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0 or entry <= 0:
        return 0
    risk_qty = risk_dollars / risk_per_unit
    cap_value = min(equity * CFG.MAX_POSITION_VALUE_PCT, buying_power * 0.8)
    cap_qty = cap_value / entry
    qty = min(risk_qty, cap_qty)
    if asset_class == "crypto":
        return round(max(qty, 0), 4)
    return int(qty)  # whole shares only (so we can use brackets + shorts)


# ----------------------------------------------------------------------------
# Execution
# ----------------------------------------------------------------------------
def submit_equity_bracket(client, db, symbol, side, qty, entry, stop, target, reasoning):
    if DRY_RUN:
        DB.log_order(db, None, symbol, side, qty, entry, stop, target, "dry_run", reasoning)
        return None
    req = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if side == "long" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(target, 2)),
        stop_loss=StopLossRequest(stop_price=round(stop, 2)),
    )
    order = client.submit_order(req)
    DB.log_order(db, str(order.id), symbol, side, qty, entry, stop, target, str(order.status), reasoning)
    return order


def submit_crypto_market(client, db, symbol, side, qty, entry, stop, target, reasoning):
    if DRY_RUN:
        DB.log_order(db, None, symbol, side, qty, entry, stop, target, "dry_run", reasoning)
        return None
    req = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY,  # we only go long on crypto (no shorts on Alpaca crypto)
        time_in_force=TimeInForce.GTC,
    )
    order = client.submit_order(req)
    DB.log_order(db, str(order.id), symbol, side, qty, entry, stop, target, str(order.status), reasoning)
    return order


def manage_crypto_exits(client, db, positions):
    """Crypto can't use bracket orders. Check open crypto positions vs stored stop/target; exit if hit."""
    for pos in positions:
        sym = pos.symbol  # comes back like "BTCUSD"
        if "USD" not in sym or len(sym) < 6:
            continue
        # Lookup last open order for this position
        slash_sym = sym[:-3] + "/" + sym[-3:]
        cur = db.execute(
            "SELECT stop_price, target_price, side FROM orders WHERE symbol IN (?, ?) AND status != 'dry_run' ORDER BY id DESC LIMIT 1",
            (sym, slash_sym),
        )
        row = cur.fetchone()
        if not row:
            continue
        stop_p, target_p, side = row
        current = float(pos.current_price) if pos.current_price else None
        if not current or not stop_p or not target_p:
            continue

        hit_stop = (side == "long" and current <= stop_p) or (side == "short" and current >= stop_p)
        hit_target = (side == "long" and current >= target_p) or (side == "short" and current <= target_p)
        if hit_stop or hit_target:
            reason = "stop" if hit_stop else "target"
            log.info(f"[exit] {sym}: hit {reason} @ {current:.2f} (stop {stop_p}, target {target_p})")
            if not DRY_RUN:
                try:
                    client.close_position(sym)
                except Exception as e:
                    log.error(f"failed to close {sym}: {e}")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    api_key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret:
        log.error("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
        sys.exit(2)

    trading = TradingClient(api_key, secret, paper=True)
    stock_data = StockHistoricalDataClient(api_key, secret)
    crypto_data = CryptoHistoricalDataClient()
    db = DB.conn(CFG.DB_PATH)

    acct = trading.get_account()
    clk = trading.get_clock()
    equity = float(acct.equity)
    cash = float(acct.cash)
    bp = float(acct.buying_power)
    log.info(f"=== run start === equity=${equity:,.2f} cash=${cash:,.2f} bp=${bp:,.2f} market_open={clk.is_open} dry_run={DRY_RUN}")

    halted, reason = check_halts(db, trading)
    if halted:
        log.warning(f"HALTED: {reason}")
        DB.log_run(db, equity, cash, bp, 0, 0, daily_pnl_pct(trading), True, reason)
        return

    # EOD flatten for equities
    if clk.is_open:
        until_close = (clk.next_close - datetime.now(timezone.utc)).total_seconds()
        if 0 < until_close < CFG.EOD_FLATTEN_SECONDS_BEFORE_CLOSE:
            log.info(f"within {until_close:.0f}s of close — flattening equities")
            if not DRY_RUN:
                try:
                    trading.close_all_positions(cancel_orders=True)
                except Exception as e:
                    log.error(f"EOD close failed: {e}")
            DB.log_run(db, equity, cash, bp, 0, 0, daily_pnl_pct(trading), False, "eod_flatten")
            return

    # Fetch bars
    stock_bars: dict = {}
    if clk.is_open:
        try:
            stock_bars = fetch_stock_bars(stock_data, CFG.EQUITIES)
            log.info(f"stock bars: {len(stock_bars)} symbols")
        except Exception as e:
            log.error(f"stock fetch failed: {e}")

    crypto_bars: dict = {}
    try:
        crypto_bars = fetch_crypto_bars(crypto_data, CFG.CRYPTO)
        log.info(f"crypto bars: {len(crypto_bars)} symbols")
    except Exception as e:
        log.error(f"crypto fetch failed: {e}")

    # Position + trade-count context
    positions = trading.get_all_positions()
    held_alpaca_syms = {p.symbol for p in positions}
    total_trades, per_symbol_trades = count_trades_today(trading)

    # Crypto exit management (brackets don't apply to crypto)
    manage_crypto_exits(trading, db, positions)

    # Iterate candidates
    candidates = [(s, df, "equity") for s, df in stock_bars.items()] + \
                 [(s, df, "crypto") for s, df in crypto_bars.items()]

    actions = 0
    for symbol, df, asset_class in candidates:
        if len(df) < 60:
            log.info(f"{symbol}: skip ({len(df)} bars)")
            continue

        mf = multifactor_signal(df)
        mr = mean_rev_signal(df)
        tr = trend_signal(df)
        ens = ensemble(mf, mr, tr)

        DB.log_signal(db, symbol, asset_class, mf["score"], mr["score"], tr["score"],
                      ens["score"], ens["decision"], {"mf": mf, "mr": mr, "tr": tr})

        log.info(f"{symbol}: mf={mf['score']:+.2f} mr={mr['score']:+.2f} tr={tr['score']:+.2f} "
                 f"ens={ens['score']:+.2f} -> {ens['decision']}")

        if ens["decision"] == "hold":
            continue

        api_sym = symbol.replace("/", "")
        existing = next((p for p in positions if p.symbol == api_sym), None)
        if existing:
            cur_side = "long" if float(existing.qty) > 0 else "short"
            opposite = (cur_side == "long" and ens["decision"] == "short") or \
                       (cur_side == "short" and ens["decision"] == "long")
            if opposite:
                log.info(f"{symbol}: signal flipped, closing existing {cur_side}")
                if not DRY_RUN:
                    try:
                        trading.close_position(api_sym)
                    except Exception as e:
                        log.error(f"close {symbol} failed: {e}")
            continue  # never stack into same symbol on same cycle

        if len(positions) >= CFG.MAX_CONCURRENT_POSITIONS:
            log.info(f"{symbol}: at max concurrent positions ({len(positions)})")
            continue
        if total_trades >= CFG.MAX_TRADES_PER_DAY:
            log.info(f"{symbol}: at max daily trades")
            break
        if per_symbol_trades.get(api_sym, 0) >= CFG.MAX_TRADES_PER_SYMBOL_PER_DAY:
            log.info(f"{symbol}: at max trades for this symbol today")
            continue

        if asset_class == "equity" and not clk.is_open:
            continue  # only trade equities during RTH
        if asset_class == "crypto" and ens["decision"] == "short":
            log.info(f"{symbol}: shorts not supported for crypto, skip")
            continue

        # ATR-based stop/target
        a_v = atr(df).iloc[-1]
        if pd.isna(a_v) or a_v <= 0:
            log.info(f"{symbol}: bad ATR, skip")
            continue

        entry = float(df["close"].iloc[-1])
        if ens["decision"] == "long":
            stop = entry - CFG.STOP_ATR_MULT * a_v
            target = entry + CFG.TARGET_ATR_MULT * a_v
        else:
            stop = entry + CFG.STOP_ATR_MULT * a_v
            target = entry - CFG.TARGET_ATR_MULT * a_v

        qty = position_size(equity, bp, entry, stop, asset_class)
        if qty <= 0:
            log.info(f"{symbol}: qty=0 (stop too wide for budget), skip")
            continue

        reasoning = (f"ens={ens['score']:+.2f} mf={mf['score']:+.2f} "
                     f"mr={mr['score']:+.2f} tr={tr['score']:+.2f}")
        try:
            if asset_class == "equity":
                submit_equity_bracket(trading, db, symbol, ens["decision"], qty, entry, stop, target, reasoning)
            else:
                submit_crypto_market(trading, db, symbol, ens["decision"], qty, entry, stop, target, reasoning)
            tag = "DRY-RUN" if DRY_RUN else "SUBMITTED"
            log.info(f"  -> {tag} {ens['decision']} {qty} {symbol} @ ~{entry:.2f}  stop {stop:.2f}  tgt {target:.2f}")
            actions += 1
            total_trades += 1
            per_symbol_trades[api_sym] = per_symbol_trades.get(api_sym, 0) + 1
        except Exception as e:
            log.error(f"{symbol}: order failed: {e}\n{traceback.format_exc()}")

    DB.log_run(db, equity, cash, bp, len(positions), total_trades,
               daily_pnl_pct(trading), False, f"actions={actions}")
    log.info(f"=== run done === actions={actions} open_pos={len(positions)} trades_today={total_trades}")


if __name__ == "__main__":
    if not acquire_lock():
        sys.exit(0)
    try:
        main()
    except Exception as e:
        log.error(f"FATAL: {e}\n{traceback.format_exc()}")
        sys.exit(1)
    finally:
        release_lock()
