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

## Deployment: GitHub Actions (primary)

The bot runs in the cloud on GitHub Actions cron — your PC doesn't need to be on.

Repo: https://github.com/viraajminhas/signal-deck-bot (public so Actions minutes are unlimited; secrets stay encrypted regardless of repo visibility)

- Workflow: `.github/workflows/bot.yml`
- Cron: `*/5 13-21 * * 1-5` (every 5 min, weekdays, 13:00–21:55 UTC; covers US RTH for both EST and EDT)
- Secrets: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (set via repo Settings → Secrets and variables → Actions)
- Variables: `DRY_RUN` (`'1'` = log only, `'0'` = live paper-trade execution; default `'1'` if unset)

### Flip from dry-run to live

```powershell
gh variable set DRY_RUN -b "0"
```

Or via the GitHub web UI: Settings → Secrets and variables → Actions → Variables → New variable → `DRY_RUN` = `0`. Takes effect on the next scheduled run; no redeploy needed.

To go back to dry-run:
```powershell
gh variable set DRY_RUN -b "1"
```

### Watching it

```powershell
# Trigger a one-off run on demand
gh workflow run "SignalDeck Bot"

# Tail the most recent run live
gh run watch

# Pull bot output from a specific run
gh run view <run-id> --log | Select-String -Pattern "===|mf=|ens|DRY-RUN|SUBMITTED|HALTED|ERROR"

# Just show recent runs
gh run list --workflow="SignalDeck Bot" --limit 10
```

Or in the [Alpaca paper dashboard](https://app.alpaca.markets/paper/dashboard/overview)
under Activity → Orders / Positions for placed trades.

### Killing it

```powershell
# Pause the workflow (preserves history, resumable later)
gh workflow disable "SignalDeck Bot"

# Re-enable
gh workflow enable "SignalDeck Bot"

# Or delete the whole repo entirely
gh repo delete viraajminhas/signal-deck-bot --yes
```

Open positions stay open until manually closed in the Alpaca dashboard.

## Local execution (secondary)

If you want to run the bot manually for one-off testing or debugging:

```powershell
# Populate .env (already done locally; not committed)
# ALPACA_API_KEY=...
# ALPACA_SECRET_KEY=...
# DRY_RUN=1

pip install -r requirements.txt
python bot.py
```

Local runs write to `bot.db` (SQLite) and `bot.log`. Inspect with:

```powershell
Get-Content .\bot.log -Tail 50 -Wait

sqlite3 .\bot.db
> SELECT ts, equity, daily_pnl_pct, trades_today, halted, notes FROM runs ORDER BY id DESC LIMIT 10;
> SELECT ts, symbol, ensemble, decision FROM signals ORDER BY id DESC LIMIT 20;
> SELECT ts, symbol, side, qty, entry_price, stop_price, target_price, status FROM orders ORDER BY id DESC LIMIT 20;
> SELECT * FROM halts ORDER BY id DESC LIMIT 5;
```

If you ever want to schedule local runs as well (e.g. for crypto coverage on weekends), `setup_scheduler.ps1` will register a Windows Task Scheduler entry. **Important: do not run both local Task Scheduler and GitHub Actions in live mode simultaneously — you'll place duplicate orders.**

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
