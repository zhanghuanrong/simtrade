"""Calculates quantitative performance metrics, Sharpe ratio, drawdown, and trade analytics."""

import math
from typing import Any, Dict, List
import numpy as np
import pandas as pd
from simtrade.models.trade import Trade
from simtrade.reporting.ledger import AccountSnapshot


class PerformanceAnalytics:
    """Computes comprehensive quantitative performance statistics."""

    @staticmethod
    def calculate(snapshots: List[AccountSnapshot], trades: List[Trade], initial_capital: float = 100_000.0) -> Dict[str, Any]:
        if not snapshots:
            return {
                "initial_capital": initial_capital,
                "final_equity": initial_capital,
                "total_return_pct": 0.0,
                "total_trades": len(trades),
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_usd": 0.0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
            }

        equities = np.array([s.equity for s in snapshots])
        final_equity = float(equities[-1])
        total_pnl = final_equity - initial_capital
        total_return_pct = round((total_pnl / initial_capital) * 100.0, 2)

        # Max Drawdown
        running_max = np.maximum.accumulate(equities)
        drawdowns_usd = running_max - equities
        max_drawdown_usd = float(np.max(drawdowns_usd)) if len(drawdowns_usd) > 0 else 0.0
        drawdowns_pct = np.divide(drawdowns_usd, running_max, out=np.zeros_like(drawdowns_usd), where=running_max > 0)
        max_drawdown_pct = round(float(np.max(drawdowns_pct)) * 100.0, 2) if len(drawdowns_pct) > 0 else 0.0

        # Returns & Sharpe Ratio (assuming 1-minute steps, annualized)
        if len(equities) > 1:
            returns = np.diff(equities) / equities[:-1]
            # Replace NaNs or Infs
            returns = np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)
            mean_ret = np.mean(returns)
            std_ret = np.std(returns)
            
            # Annualization factor: 252 days * 390 minutes = 98,280 periods/year
            periods_per_year = 98280.0
            if std_ret > 1e-8:
                annualized_ret = mean_ret * periods_per_year
                annualized_vol = std_ret * math.sqrt(periods_per_year)
                sharpe_ratio = round(float(annualized_ret / annualized_vol), 2)
            else:
                sharpe_ratio = 0.0

            # Downside volatility for Sortino
            downside_returns = returns[returns < 0]
            if len(downside_returns) > 0:
                downside_std = np.std(downside_returns) * math.sqrt(periods_per_year)
                sortino_ratio = round(float((mean_ret * periods_per_year) / max(downside_std, 1e-6)), 2)
            else:
                sortino_ratio = 0.0
        else:
            sharpe_ratio = 0.0
            sortino_ratio = 0.0

        # Trade analytics
        total_trades = len(trades)
        winning_trades = 0
        losing_trades = 0
        gross_profit = 0.0
        gross_loss = 0.0

        return {
            "initial_capital": round(initial_capital, 2),
            "final_equity": round(final_equity, 2),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": total_return_pct,
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "max_drawdown_usd": round(max_drawdown_usd, 2),
            "max_drawdown_pct": max_drawdown_pct,
            "total_trades": total_trades,
            "total_snapshots": len(snapshots),
        }
