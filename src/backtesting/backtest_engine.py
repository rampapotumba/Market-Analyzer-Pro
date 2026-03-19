"""BacktestEngine v4 — candle-by-candle historical simulation (SIM-22).

Architecture (per SPEC_SIMULATOR_V4.md §SIM-22):
  1. create_backtest_run (status=running)
  2. Load price_data for each symbol in [start_date, end_date]
  3. Iterate candles chronologically — NO lookahead (slice [0..i-1])
  4. Compute TA score + regime from slice; fa/sentiment/geo = 0.0 (neutral per SIM-17)
  5. Generate signal (SignalEngineV2 core logic without LLM/DB guards)
  6. Entry fill on NEXT candle open price
  7. SL/TP check per SIM-09 logic (high/low of candle) — worst case when both hit = SL
  8. Accumulate BacktestTradeResult list in memory
  9. Bulk insert → backtest_trades
  10. Compute summary: win_rate, PF, max_drawdown, equity_curve, monthly_returns
  11. Update backtest_run (status=completed, summary=...)

Isolation guarantee: this module NEVER writes to signal_results or virtual_portfolio.
"""

import datetime
import logging
from decimal import Decimal
from typing import Any, Optional

import pandas as pd

from sqlalchemy.ext.asyncio import AsyncSession

from src.backtesting.backtest_params import BacktestParams, BacktestTradeResult
from src.database.crud import (
    create_backtest_run,
    create_backtest_trades_bulk,
    get_instrument_by_symbol,
    get_price_data,
    update_backtest_run,
)
from src.signals.risk_manager_v2 import REGIME_RR_MAP, ATR_SL_MULTIPLIER_MAP, RiskManagerV2

logger = logging.getLogger(__name__)

# ── Thresholds (mirror SignalEngineV2) ────────────────────────────────────────
_BUY_THRESHOLD = 10.0     # composite score to emit a signal
_SELL_THRESHOLD = -10.0

# Minimum bars needed before first signal attempt
_MIN_BARS_HISTORY = 50

# Weight used for TA when fa/sentiment/geo = 0.0
# Mirrors DEFAULT_TA_WEIGHT in settings (0.45 default)
_TA_WEIGHT = 0.45

# Market type → pip size for P&L calculation
_PIP_SIZE: dict[str, Decimal] = {
    "forex": Decimal("0.0001"),
    "crypto": Decimal("0.01"),
    "stocks": Decimal("0.01"),
}

# Slippage for SL exits (mirrors SIM-10)
_SL_SLIPPAGE: dict[str, Decimal] = {
    "forex": Decimal("0.0001"),   # 1 pip
    "stocks": Decimal("0.01"),    # 1 pip (dollar)
    "crypto": Decimal("0.001"),   # 0.1%
}


def _detect_regime_from_df(df: pd.DataFrame) -> str:
    """Lightweight regime detection from a price DataFrame.

    Returns one of: STRONG_TREND_BULL, STRONG_TREND_BEAR, TREND_BULL, TREND_BEAR,
                    RANGING, VOLATILE, DEFAULT.
    Uses ADX and SMA-200 — matches RegimeDetector._detect_regime() logic.
    """
    from src.analysis.regime_detector import RegimeDetector

    rd = RegimeDetector()
    regime, _adx, _atr_pct = rd._detect_regime(df, vix=None)

    # Normalise to keys used by ATR_SL_MULTIPLIER_MAP / REGIME_RR_MAP
    _MAP = {
        "HIGH_VOLATILITY": "VOLATILE",
        "LOW_VOLATILITY": "RANGING",
        "WEAK_TREND_BULL": "TREND_BULL",
        "WEAK_TREND_BEAR": "TREND_BEAR",
        "STRONG_TREND_BULL": "STRONG_TREND_BULL",
        "STRONG_TREND_BEAR": "STRONG_TREND_BEAR",
        "RANGING": "RANGING",
        "VOLATILE": "VOLATILE",
    }
    return _MAP.get(regime, "DEFAULT")


def _to_ohlcv_df(price_rows: list) -> pd.DataFrame:
    """Convert price_data ORM rows to pandas DataFrame for TAEngine."""
    records = [
        {
            "timestamp": row.timestamp,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume or 0),
        }
        for row in price_rows
    ]
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df = df.set_index("timestamp").sort_index()
    return df


def _compute_sl_exit_price(
    sl_price: Decimal,
    direction: str,
    market_type: str,
    entry_price: Decimal,
) -> Decimal:
    """Apply SL slippage per SIM-10."""
    slip = _SL_SLIPPAGE.get(market_type, Decimal("0.0001"))
    if market_type == "crypto":
        slip = entry_price * Decimal("0.001")  # 0.1% of entry
    if direction == "LONG":
        return sl_price - slip
    else:
        return sl_price + slip


def _compute_pnl(
    direction: str,
    entry: Decimal,
    exit_price: Decimal,
    position_pct: Decimal,
    account_size: Decimal,
    market_type: str,
) -> tuple[Decimal, Decimal]:
    """Return (pnl_pips, pnl_usd)."""
    pip = _PIP_SIZE.get(market_type, Decimal("0.0001"))
    if direction == "LONG":
        price_move = exit_price - entry
    else:
        price_move = entry - exit_price

    pnl_pips = price_move / pip
    risk_usd = account_size * position_pct / Decimal("100")
    sl_dist = None  # estimated below if needed

    # Simplified P&L: proportional to price move relative to position size
    # pnl_usd = account * size_pct% * (move / entry)  (approximate)
    if entry > 0:
        pnl_usd = risk_usd * (price_move / entry) * Decimal("100")
    else:
        pnl_usd = Decimal("0")

    return pnl_pips.quantize(Decimal("0.0001")), pnl_usd.quantize(Decimal("0.0001"))


def _compute_summary(
    trades: list[BacktestTradeResult],
    account_size: Decimal,
) -> dict[str, Any]:
    """Compute aggregate backtest statistics."""
    total = len(trades)
    wins = [t for t in trades if t.result == "win"]
    losses = [t for t in trades if t.result == "loss"]

    win_rate = (len(wins) / total * 100) if total > 0 else 0.0
    total_pnl = sum(float(t.pnl_usd or 0) for t in trades)
    gross_win = sum(float(t.pnl_usd or 0) for t in wins)
    gross_loss = abs(sum(float(t.pnl_usd or 0) for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    avg_dur = (
        sum(t.duration_minutes or 0 for t in trades) / total if total > 0 else 0
    )

    long_count = sum(1 for t in trades if t.direction == "LONG")
    short_count = sum(1 for t in trades if t.direction == "SHORT")

    # Equity curve: running balance
    balance = float(account_size)
    equity_curve: list[dict] = []
    for t in sorted(trades, key=lambda x: x.exit_at or datetime.datetime.min):
        balance += float(t.pnl_usd or 0)
        equity_curve.append({
            "date": (t.exit_at.isoformat() if t.exit_at else ""),
            "balance": round(balance, 4),
        })

    # Max drawdown
    peak = float(account_size)
    max_dd = 0.0
    running = float(account_size)
    for t in sorted(trades, key=lambda x: x.exit_at or datetime.datetime.min):
        running += float(t.pnl_usd or 0)
        if running > peak:
            peak = running
        dd = (peak - running) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Monthly returns
    monthly: dict[str, dict] = {}
    for t in trades:
        if t.exit_at:
            key = t.exit_at.strftime("%Y-%m")
            if key not in monthly:
                monthly[key] = {"month": key, "pnl_usd": 0.0, "trades": 0}
            monthly[key]["pnl_usd"] += float(t.pnl_usd or 0)
            monthly[key]["trades"] += 1

    # By symbol
    by_symbol: dict[str, dict] = {}
    for t in trades:
        s = t.symbol
        if s not in by_symbol:
            by_symbol[s] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        by_symbol[s]["trades"] += 1
        if t.result == "win":
            by_symbol[s]["wins"] += 1
        by_symbol[s]["pnl_usd"] += float(t.pnl_usd or 0)

    # By score bucket (mirrors SIM-14 buckets)
    def _score_bucket(score: Optional[Decimal]) -> str:
        if score is None:
            return "unknown"
        s = float(score)
        if s >= 15:
            return "strong_buy"
        if s >= 10:
            return "buy"
        if s >= 7:
            return "weak_buy"
        if s <= -15:
            return "strong_sell"
        if s <= -10:
            return "sell"
        if s <= -7:
            return "weak_sell"
        return "neutral"

    by_score: dict[str, dict] = {}
    for t in trades:
        bucket = _score_bucket(t.composite_score)
        if bucket not in by_score:
            by_score[bucket] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        by_score[bucket]["trades"] += 1
        if t.result == "win":
            by_score[bucket]["wins"] += 1
        by_score[bucket]["pnl_usd"] += float(t.pnl_usd or 0)

    return {
        "total_trades": total,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "total_pnl_usd": round(total_pnl, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "avg_duration_minutes": round(avg_dur, 1),
        "long_count": long_count,
        "short_count": short_count,
        "equity_curve": equity_curve,
        "monthly_returns": sorted(monthly.values(), key=lambda x: x["month"]),
        "by_symbol": by_symbol,
        "by_score_bucket": by_score,
    }


class BacktestEngine:
    """Candle-by-candle backtest engine (SIM-22).

    Isolation guarantee: NEVER writes to signal_results or virtual_portfolio.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._rm = RiskManagerV2()

    async def run_backtest(self, params: BacktestParams) -> str:
        """
        Run a full backtest. Creates a run record, simulates candle-by-candle,
        persists results, and returns the run_id (UUID string).
        """
        run_id = await create_backtest_run(self.db, params.model_dump(mode="json"))
        await update_backtest_run(self.db, run_id, "running")
        await self.db.commit()

        try:
            trades = await self._simulate(params)
            summary = _compute_summary(trades, params.account_size)

            trade_dicts = [
                {
                    "symbol": t.symbol,
                    "timeframe": t.timeframe,
                    "direction": t.direction,
                    "entry_price": str(t.entry_price),
                    "exit_price": str(t.exit_price) if t.exit_price is not None else None,
                    "exit_reason": t.exit_reason,
                    "pnl_pips": str(t.pnl_pips) if t.pnl_pips is not None else None,
                    "pnl_usd": str(t.pnl_usd) if t.pnl_usd is not None else None,
                    "result": t.result,
                    "composite_score": str(t.composite_score) if t.composite_score is not None else None,
                    "entry_at": t.entry_at,
                    "exit_at": t.exit_at,
                    "duration_minutes": t.duration_minutes,
                    "mfe": str(t.mfe) if t.mfe is not None else None,
                    "mae": str(t.mae) if t.mae is not None else None,
                }
                for t in trades
            ]

            await create_backtest_trades_bulk(self.db, run_id, trade_dicts)
            await update_backtest_run(self.db, run_id, "completed", summary)
            await self.db.commit()

            logger.info(
                "[SIM-22] Backtest %s completed: %d trades, win_rate=%.1f%%, PF=%s",
                run_id,
                summary["total_trades"],
                summary["win_rate_pct"],
                summary.get("profit_factor"),
            )
        except Exception as exc:
            logger.error("[SIM-22] Backtest %s failed: %s", run_id, exc, exc_info=True)
            await update_backtest_run(self.db, run_id, "failed", {"error": str(exc)})
            await self.db.commit()

        return run_id

    async def _simulate(self, params: BacktestParams) -> list[BacktestTradeResult]:
        """Core simulation loop. Returns list of completed trades."""
        trades: list[BacktestTradeResult] = []

        start_dt = datetime.datetime.fromisoformat(params.start_date).replace(
            tzinfo=datetime.timezone.utc
        )
        end_dt = datetime.datetime.fromisoformat(params.end_date).replace(
            hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc
        )

        for symbol in params.symbols:
            instrument = await get_instrument_by_symbol(self.db, symbol)
            if instrument is None:
                logger.warning("[SIM-22] Instrument %s not found — skipping", symbol)
                continue

            market_type: str = instrument.market or "forex"

            price_rows = await get_price_data(
                self.db,
                instrument.id,
                params.timeframe,
                from_dt=start_dt,
                to_dt=end_dt,
                limit=100_000,
            )

            if len(price_rows) < _MIN_BARS_HISTORY + 2:
                logger.warning(
                    "[SIM-22] %s: only %d candles — need at least %d, skipping",
                    symbol, len(price_rows), _MIN_BARS_HISTORY + 2,
                )
                continue

            symbol_trades = await self._simulate_symbol(
                symbol=symbol,
                market_type=market_type,
                timeframe=params.timeframe,
                price_rows=price_rows,
                account_size=params.account_size,
                apply_slippage=params.apply_slippage,
            )
            trades.extend(symbol_trades)

        return trades

    async def _simulate_symbol(
        self,
        symbol: str,
        market_type: str,
        timeframe: str,
        price_rows: list,
        account_size: Decimal,
        apply_slippage: bool,
    ) -> list[BacktestTradeResult]:
        """Simulate one symbol over all its candles. Returns closed trades."""
        trades: list[BacktestTradeResult] = []
        open_trade: Optional[dict[str, Any]] = None  # one position at a time per symbol

        n = len(price_rows)

        for i in range(_MIN_BARS_HISTORY, n - 1):
            # ── NO LOOKAHEAD: only rows [0..i-1] visible to signal generation
            history = price_rows[:i]
            df = _to_ohlcv_df(history)
            current_candle = price_rows[i]
            next_candle = price_rows[i + 1]

            # ── Check open trade SL/TP on current candle ──────────────────────
            if open_trade is not None:
                closed = self._check_exit(
                    open_trade=open_trade,
                    candle=current_candle,
                    market_type=market_type,
                    apply_slippage=apply_slippage,
                )
                if closed:
                    trades.append(closed)
                    open_trade = None
                else:
                    # Update MAE/MFE
                    candle_high = float(current_candle.high)
                    candle_low = float(current_candle.low)
                    direction = open_trade["direction"]
                    entry = float(open_trade["entry_price"])
                    if direction == "LONG":
                        mfe_candidate = candle_high - entry
                        mae_candidate = entry - candle_low
                    else:
                        mfe_candidate = entry - candle_low
                        mae_candidate = candle_high - entry
                    if mfe_candidate > float(open_trade["mfe"]):
                        open_trade["mfe"] = mfe_candidate
                    if mae_candidate > float(open_trade["mae"]):
                        open_trade["mae"] = mae_candidate

            # ── Generate signal from history slice ────────────────────────────
            if open_trade is not None:
                continue  # one position per symbol at a time

            signal = self._generate_signal(df, symbol, market_type, timeframe)
            if signal is None:
                continue

            # ── Entry on NEXT candle open (no lookahead) ──────────────────────
            entry_price = Decimal(str(next_candle.open))
            entry_at = next_candle.timestamp
            direction = signal["direction"]

            sl, tp = self._recalc_sl_tp(
                entry=entry_price,
                atr=signal["atr"],
                direction=direction,
                regime=signal["regime"],
            )
            if sl is None or tp is None:
                continue

            open_trade = {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "entry_price": entry_price,
                "entry_at": entry_at,
                "stop_loss": sl,
                "take_profit": tp,
                "composite_score": signal["composite_score"],
                "position_pct": signal["position_pct"],
                "mfe": 0.0,
                "mae": 0.0,
            }
            # Skip to next candle
            i += 1

        # ── If position still open at end of data — close at last price ───────
        if open_trade is not None:
            last = price_rows[-1]
            exit_price = Decimal(str(last.close))
            pnl_pips, pnl_usd = _compute_pnl(
                open_trade["direction"],
                open_trade["entry_price"],
                exit_price,
                Decimal(str(open_trade["position_pct"])),
                account_size,
                market_type,
            )
            dur = None
            if open_trade["entry_at"] and last.timestamp:
                dur = int(
                    (last.timestamp - open_trade["entry_at"]).total_seconds() / 60
                )
            trades.append(BacktestTradeResult(
                symbol=symbol,
                timeframe=timeframe,
                direction=open_trade["direction"],
                entry_price=open_trade["entry_price"],
                exit_price=exit_price,
                exit_reason="end_of_data",
                pnl_pips=pnl_pips,
                pnl_usd=pnl_usd,
                result="win" if pnl_usd >= 0 else "loss",
                composite_score=open_trade.get("composite_score"),
                entry_at=open_trade["entry_at"],
                exit_at=last.timestamp,
                duration_minutes=dur,
                mfe=Decimal(str(round(open_trade["mfe"], 8))),
                mae=Decimal(str(round(open_trade["mae"], 8))),
            ))

        return trades

    def _check_exit(
        self,
        open_trade: dict[str, Any],
        candle: Any,
        market_type: str,
        apply_slippage: bool,
    ) -> Optional[BacktestTradeResult]:
        """
        SIM-09 logic: check SL and TP by candle high/low.
        Worst case: if both SL and TP are breached → exit at SL.
        Returns a closed BacktestTradeResult or None if still open.
        """
        direction = open_trade["direction"]
        sl = open_trade["stop_loss"]
        tp = open_trade["take_profit"]
        entry = open_trade["entry_price"]
        candle_high = Decimal(str(candle.high))
        candle_low = Decimal(str(candle.low))

        sl_hit = False
        tp_hit = False

        if direction == "LONG":
            sl_hit = candle_low <= sl
            tp_hit = candle_high >= tp
        else:
            sl_hit = candle_high >= sl
            tp_hit = candle_low <= tp

        if not sl_hit and not tp_hit:
            return None

        # Worst case (SIM-06): if both hit → SL
        if sl_hit:
            exit_price = sl
            if apply_slippage:
                exit_price = _compute_sl_exit_price(sl, direction, market_type, entry)
            exit_reason = "sl_hit"
        else:
            exit_price = tp
            exit_reason = "tp_hit"

        pnl_pips, pnl_usd = _compute_pnl(
            direction, entry, exit_price,
            Decimal(str(open_trade["position_pct"])),
            Decimal("1000"),  # account size for P&L pct calculation
            market_type,
        )

        dur = None
        if open_trade["entry_at"] and candle.timestamp:
            dur = int(
                (candle.timestamp - open_trade["entry_at"]).total_seconds() / 60
            )

        return BacktestTradeResult(
            symbol=open_trade["symbol"],
            timeframe=open_trade["timeframe"],
            direction=direction,
            entry_price=entry,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl_pips=pnl_pips,
            pnl_usd=pnl_usd,
            result="win" if pnl_usd >= 0 else "loss",
            composite_score=open_trade.get("composite_score"),
            entry_at=open_trade["entry_at"],
            exit_at=candle.timestamp,
            duration_minutes=dur,
            mfe=Decimal(str(round(open_trade["mfe"], 8))),
            mae=Decimal(str(round(open_trade["mae"], 8))),
        )

    def _generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        market_type: str,
        timeframe: str,
    ) -> Optional[dict[str, Any]]:
        """
        Lightweight signal generation from a DataFrame slice.

        Mirrors SignalEngineV2.generate() core logic:
        - ta_score via TAEngine
        - fa/sentiment/geo = 0.0 (no historical data available in backtest)
        - composite = TA weight × ta_score (neutral others)
        - threshold: |composite| >= 10 to emit signal

        LLM and DB guards are deliberately skipped (no historical context).
        """
        from src.analysis.ta_engine import TAEngine

        try:
            ta_engine = TAEngine(df, timeframe=timeframe)
            ta_score = ta_engine.calculate_ta_score()
            atr = ta_engine.get_atr(14)
        except Exception as exc:
            logger.debug("[SIM-22] TAEngine error for %s: %s", symbol, exc)
            return None

        if atr is None or atr <= 0:
            return None

        composite = _TA_WEIGHT * ta_score

        if abs(composite) < abs(_BUY_THRESHOLD):
            return None

        direction = "LONG" if composite > 0 else "SHORT"

        regime = "DEFAULT"
        try:
            regime = _detect_regime_from_df(df)
        except Exception as exc:
            logger.debug("[SIM-22] Regime detection error for %s: %s", symbol, exc)

        return {
            "direction": direction,
            "composite_score": Decimal(str(round(composite, 4))),
            "ta_score": ta_score,
            "regime": regime,
            "atr": atr,
            "position_pct": 2.0,  # fixed 2% risk per SIM-19 default
        }

    def _recalc_sl_tp(
        self,
        entry: Decimal,
        atr: Decimal,
        direction: str,
        regime: str,
    ) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """Compute SL and TP for the backtest position using regime-adaptive rules."""
        try:
            levels = self._rm.calculate_levels_for_regime(
                entry=entry,
                atr=atr,
                direction=direction,
                regime=regime,
            )
            return levels.get("stop_loss"), levels.get("take_profit_1")
        except Exception as exc:
            logger.debug("[SIM-22] RiskManager error: %s", exc)
            return None, None
