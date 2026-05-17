"""SQLite logging for signals, orders, halts, and per-run snapshots.

Inspect with:
  sqlite3 bot.db
  > select * from runs order by id desc limit 10;
  > select * from orders order by id desc limit 20;
  > select symbol, decision, ensemble, ts from signals order by id desc limit 50;
"""
import json
import sqlite3
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    multifactor REAL,
    mean_rev REAL,
    trend REAL,
    ensemble REAL,
    decision TEXT,
    details TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    alpaca_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL,
    stop_price REAL,
    target_price REAL,
    status TEXT,
    reasoning TEXT
);
CREATE TABLE IF NOT EXISTS halts (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    equity REAL,
    cash REAL,
    buying_power REAL,
    open_positions INTEGER,
    trades_today INTEGER,
    daily_pnl_pct REAL,
    halted INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders(ts);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
"""


def conn(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_signal(c, symbol, asset_class, mf, mr, tr, ens, decision, details):
    c.execute(
        "INSERT INTO signals (ts, symbol, asset_class, multifactor, mean_rev, trend, ensemble, decision, details) VALUES (?,?,?,?,?,?,?,?,?)",
        (_now(), symbol, asset_class, mf, mr, tr, ens, decision, json.dumps(details, default=str)),
    )
    c.commit()


def log_order(c, alpaca_id, symbol, side, qty, entry, stop, target, status, reasoning):
    c.execute(
        "INSERT INTO orders (ts, alpaca_id, symbol, side, qty, entry_price, stop_price, target_price, status, reasoning) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_now(), alpaca_id, symbol, side, qty, entry, stop, target, status, reasoning),
    )
    c.commit()


def log_halt(c, reason):
    c.execute("INSERT INTO halts (ts, reason) VALUES (?, ?)", (_now(), reason))
    c.commit()


def log_run(c, equity, cash, buying_power, open_pos, trades_today, daily_pnl_pct, halted, notes):
    c.execute(
        "INSERT INTO runs (ts, equity, cash, buying_power, open_positions, trades_today, daily_pnl_pct, halted, notes) VALUES (?,?,?,?,?,?,?,?,?)",
        (_now(), equity, cash, buying_power, open_pos, trades_today, daily_pnl_pct, int(halted), notes),
    )
    c.commit()


def count_trades_today(c) -> tuple[int, dict]:
    """Return (total_trades_today, {symbol: count})."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cur = c.execute(
        "SELECT symbol, COUNT(*) FROM orders WHERE ts LIKE ? || '%' AND status != 'dry_run' GROUP BY symbol",
        (today,),
    )
    per = {row[0]: row[1] for row in cur.fetchall()}
    return sum(per.values()), per


def last_n_pnl_signs(c, n: int = 3) -> list[int]:
    """Last N orders' realized P&L sign (-1/0/+1). Pulled from Alpaca activities at runtime; this is a stub for DB-side queries."""
    return []
