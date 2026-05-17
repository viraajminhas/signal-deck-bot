"""SIGNAL DECK bot configuration.

All knobs in one place. Edit and re-run — no other files need touching for
strategy tuning (universe, risk caps, signal weights, thresholds).
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    # ---- Universe ----
    EQUITIES: tuple = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOG", "TSLA")
    CRYPTO: tuple = ("BTC/USD", "ETH/USD")

    # ---- Risk caps (Moderate profile) ----
    RISK_PER_TRADE_PCT: float = 0.02          # 2% of equity at risk per trade
    MAX_DAILY_LOSS_PCT: float = 0.05          # halt for the day if equity down 5%
    MAX_CONCURRENT_POSITIONS: int = 6
    MAX_TRADES_PER_DAY: int = 20
    MAX_TRADES_PER_SYMBOL_PER_DAY: int = 3
    CONSECUTIVE_LOSS_HALT: int = 3            # halt after 3 consecutive losers
    MAX_POSITION_VALUE_PCT: float = 0.25      # cap any one position at 25% of equity

    # ---- Strategy ----
    BAR_LOOKBACK_DAYS: int = 5
    BAR_TIMEFRAME_MIN: int = 5                # 5-min bars
    MIN_ENSEMBLE_SCORE: float = 0.40          # min |score| to take a trade
    SIGNAL_WEIGHTS: dict = field(default_factory=lambda: {
        "multifactor": 0.40,
        "mean_rev":   0.30,
        "trend":      0.30,
    })

    # ---- Stops & targets (ATR-based) ----
    STOP_ATR_MULT: float = 1.5
    TARGET_ATR_MULT: float = 3.0              # 2:1 reward:risk

    # ---- Data ----
    STOCK_DATA_LAG_MIN: int = 20              # free-tier IEX is ~15-min delayed; pad to 20

    # ---- Files ----
    DB_PATH: str = "bot.db"
    LOG_PATH: str = "bot.log"
    LOCK_PATH: str = "bot.lock"

    # ---- EOD ----
    EOD_FLATTEN_SECONDS_BEFORE_CLOSE: int = 300   # close equities 5 min before bell


CFG = Config()
