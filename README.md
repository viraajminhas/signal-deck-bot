# SignalDeck Trading Bot

Automated paper-trading bot for Alpaca. Runs every 5 minutes on weekdays, scores
each symbol with a three-strategy ensemble (multifactor momentum, mean reversion,
trend following), and submits bracket orders with ATR-based stops and a 2:1
reward:risk target. Hard kill-switches halt the bot on daily loss or trade limits.

**Paper trading only. Not financial advice.** Strategy edge has not been
backtested. Paper P&L overstates real-world performance (perfect fills, no
slippage, no rejections). Treat results as directional, not predictive.

## What it trades

- **Equities** (RTH only): SPY, QQQ, AAPL, MSFT, NVDA, AMZN, META, GOOG, TSLA
- **Crypto** (whenever bot runs): BTC/USD, ETH/USD

Edit `config.py` to change the universe.

## Risk profile (Moderate)

| Setting | Value | Notes |
|---|---|---|
| Per-trade risk | 2% of equity | Sized via ATR-based stop distance |
| Position cap | 25% of equity / 80% of buying power | Whichever is smaller |
| Max daily loss | 5% of equity | Bot halts itself for the day |
| Max concurrent positions | 6 | |
| Max trades per day | 20 | |
| Max trades per symbol per day | 3 | |
| Stop distance | 1.5 × ATR(14) | Tighter than typical day-trade stops |
| Target distance | 3.0 × ATR(14) | 2:1 reward:risk |

## Strategy

Each cycle, every symbol gets scored by three independent signals in `[-1, +1]`:

1. **Multifactor** (`weight 0.40`) — SIGNAL DECK style: MACD direction + RSI zone (>60 / <40) + price vs session VWAP + volume confirmation.
2. **Mean reversion** (`weight 0.30`) — RSI <30 + price below lower Bollinger = long; mirror for short. Suppressed when ADX > 30 (don't fight strong trends).
3. **Trend following** (`weight 0.30`) — SMA(9)/SMA(21)/SMA(50) stack + ADX > 20 confirmation.

**Ensemble**: weighted vote. If two signals disagree, the score is halved. A
trade fires only when `|ensemble score| ≥ 0.40`.

**Existing positions**: never stack into the same symbol on one cycle. If the
ensemble flips sign, the position is closed (not reversed in same cycle).

## Setup

1. **Install dependencies** (already done if you can run `python bot.py`):
   ```powershell
   pip install -r requirements.txt
   ```

2. **Check `.env`** — already populated with your paper-trading keys:
   ```
   ALPACA_API_KEY=...
   ALPACA_SECRET_KEY=...
   DRY_RUN=1
   ```
   Leave `DRY_RUN=1` until you've watched a few live cycles. The bot will log
   what it *would* have done without sending orders.

3. **Dry-run once manually** to confirm wiring:
   ```powershell
   python bot.py
   ```
   Inspect `bot.log` and `bot.db`.

4. **Register with Task Scheduler** (elevated PowerShell):
   ```powershell
   PowerShell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1
   ```
   Cadence: every 5 minutes, Mon–Fri, 6:00 AM – 8:00 PM local time. (The bot
   self-gates on Alpaca's market clock, so wide local hours cover ET RTH from
   any US timezone and let crypto trade outside RTH.)

5. **Flip to live paper execution** when you're satisfied:
   - Edit `.env` → `DRY_RUN=0`
   - No restart needed; next scheduled run picks it up.

## Watching it

```powershell
# Tail the log live
Get-Content .\bot.log -Tail 50 -Wait

# Inspect the DB
sqlite3 .\bot.db
> SELECT ts, equity, daily_pnl_pct, trades_today, halted, notes FROM runs ORDER BY id DESC LIMIT 10;
> SELECT ts, symbol, ensemble, decision FROM signals ORDER BY id DESC LIMIT 20;
> SELECT ts, symbol, side, qty, entry_price, stop_price, target_price, status FROM orders ORDER BY id DESC LIMIT 20;
> SELECT * FROM halts ORDER BY id DESC LIMIT 5;
```

Or in the [Alpaca paper dashboard](https://app.alpaca.markets/paper/dashboard/overview)
under Activity → Orders / Positions.

## Limitations to know about

- **Free-tier data is delayed ~15 minutes** (IEX feed). The bot adds a 20-min
  lag buffer when requesting bars. Decisions are based on slightly stale data;
  fills will be at live prices. Generally fine for 5-min cadence on liquid
  instruments, but a known source of slippage.
- **Crypto can't use bracket orders on Alpaca.** The bot stores the stop/target
  in `bot.db` and exits the position on the next cycle if hit. Worst case lag
  is one cycle (~5 min).
- **No crypto shorts** (Alpaca paper crypto is long-only).
- **EOD flatten** — equities are force-closed within 5 minutes of the bell to
  avoid overnight gap risk. Crypto positions persist across days.
- **Halt-on-consecutive-losses** is stubbed (not wired). The daily-loss cap (5%)
  is the real safety net.
- **OneDrive sync**: `.env` lives in your OneDrive folder and will sync to the
  cloud. Acceptable for paper keys; if you ever rotate to live keys, move them
  outside OneDrive.

## File map

| File | What it does |
|---|---|
| `bot.py` | Main entry — fetches data, runs strategies, sizes, executes, logs |
| `config.py` | All tunable parameters (universe, risk caps, weights, thresholds) |
| `strategies.py` | The three signals + ensemble vote |
| `indicators.py` | Pure pandas/numpy: MACD, RSI, VWAP, SMA, Bollinger, ATR, ADX |
| `db.py` | SQLite schema and logging helpers |
| `setup_scheduler.ps1` | Registers/unregisters the Task Scheduler job |
| `.env` | API keys + DRY_RUN flag |
| `bot.db` | SQLite log (auto-created) |
| `bot.log` | Text log (auto-created, append-only) |
| `bot.lock` | Concurrency lock (auto-managed) |

## Tuning

- Want more trades? Lower `MIN_ENSEMBLE_SCORE` (0.40 → 0.30).
- Want tighter risk? Lower `RISK_PER_TRADE_PCT` (0.02 → 0.01).
- Want a different mix? Adjust `SIGNAL_WEIGHTS` — they don't need to sum to 1.
- Want a narrower universe? Edit `EQUITIES` / `CRYPTO` tuples in `config.py`.

## Killing the bot

```powershell
schtasks /Delete /TN SignalDeckBot /F
```

Open positions stay open until manually closed in the Alpaca dashboard or via:

```python
from alpaca.trading.client import TradingClient
c = TradingClient(KEY, SECRET, paper=True)
c.close_all_positions(cancel_orders=True)
```
