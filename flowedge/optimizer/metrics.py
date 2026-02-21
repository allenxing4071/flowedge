"""
绩效指标计算器 — 量化交易策略的标准评估指标。

核心指标：
  1. Sharpe Ratio: 风险调整收益（年化）
  2. Max Drawdown: 最大回撤
  3. Win Rate: 胜率
  4. Profit Factor: 盈亏比（总盈利/总亏损）
  5. Calmar Ratio: 年化收益/最大回撤
  6. Total PnL: 总盈亏
  7. Average PnL: 平均每笔盈亏
  8. Expectancy: 期望值（平均盈利×胜率 - 平均亏损×败率）
  9. IC (Information Coefficient): 信号得分与实际收益的相关性

学术基础：
  - Sharpe (1966): 风险调整收益度量
  - López de Prado (2018): Deflated Sharpe Ratio（防过拟合）
  - 量化交易行业标准指标集
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# 小样本高频数据下，Sharpe 容易被年化和低波动率放大，这里加稳态保护。
MIN_TRADES_FOR_SHARPE = 20
SHARPE_SIGMA_FLOOR = 0.12
MIN_PERIOD_DAYS = 1.0
MAX_ANNUAL_TRADES_PER_DAY = 120.0
MAX_ABS_SHARPE = 12.0


@dataclass
class TradeResult:
    """单笔交易结果（用于指标计算）"""
    pnl_pct: float          # 盈亏百分比
    signal: str             # BUY / SELL
    score: float            # 信号得分
    confidence: float       # 置信度
    entry_price: float = 0
    exit_price: float = 0
    hold_time_s: float = 0  # 持仓时间（秒）


@dataclass
class PerformanceMetrics:
    """策略绩效指标集"""
    # 基础统计
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    # 盈亏
    total_pnl_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    max_win_pct: float = 0.0
    max_loss_pct: float = 0.0

    # 风险指标
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    # 信号质量
    ic_score: float = 0.0       # 信号得分与收益的相关系数
    ic_confidence: float = 0.0  # 置信度与收益的相关系数

    # 连续统计
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    # 元信息
    period_days: float = 0.0
    avg_hold_time_s: float = 0.0


def calculate_metrics(
    trades: list[TradeResult],
    period_days: Optional[float] = None,
    risk_free_rate: float = 0.0,
) -> PerformanceMetrics:
    """
    计算策略绩效指标。

    参数:
        trades: 交易结果列表
        period_days: 交易期间天数（用于年化计算）
        risk_free_rate: 无风险利率（年化，默认 0）

    返回:
        PerformanceMetrics
    """
    m = PerformanceMetrics()

    if not trades:
        return m

    m.total_trades = len(trades)
    pnls = [t.pnl_pct for t in trades]
    period_days_safe = max(period_days or MIN_PERIOD_DAYS, MIN_PERIOD_DAYS)

    # ── 基础统计 ──
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    m.winning_trades = len(wins)
    m.losing_trades = len(losses)
    m.win_rate = m.winning_trades / m.total_trades if m.total_trades > 0 else 0

    # ── 盈亏 ──
    m.total_pnl_pct = sum(pnls)
    m.avg_pnl_pct = m.total_pnl_pct / m.total_trades if m.total_trades > 0 else 0
    m.avg_win_pct = sum(wins) / len(wins) if wins else 0
    m.avg_loss_pct = sum(losses) / len(losses) if losses else 0
    m.max_win_pct = max(pnls) if pnls else 0
    m.max_loss_pct = min(pnls) if pnls else 0

    # ── Sharpe Ratio ──
    if len(pnls) >= MIN_TRADES_FOR_SHARPE:
        mean_pnl = sum(pnls) / len(pnls)
        std_pnl = max(_std(pnls), SHARPE_SIGMA_FLOOR)
        if std_pnl > 0:
            # 年化：对交易频率做上限保护，避免超高频样本造成 Sharpe 失真
            trades_per_day = m.total_trades / period_days_safe
            trades_per_day = min(trades_per_day, MAX_ANNUAL_TRADES_PER_DAY)
            annualization = math.sqrt(trades_per_day * 365)
            raw_sharpe = (mean_pnl - risk_free_rate / 365) / std_pnl * annualization
            raw_sharpe = max(-MAX_ABS_SHARPE, min(MAX_ABS_SHARPE, raw_sharpe))
            m.sharpe_ratio = round(raw_sharpe, 4)

    # ── Max Drawdown ──
    m.max_drawdown_pct = _max_drawdown(pnls)

    # ── Calmar Ratio ──
    if m.max_drawdown_pct < 0:
        annual_return = m.total_pnl_pct * (365 / period_days_safe)
        m.calmar_ratio = round(annual_return / abs(m.max_drawdown_pct), 4)

    # ── Profit Factor ──
    total_wins = sum(wins) if wins else 0
    total_losses = abs(sum(losses)) if losses else 0
    m.profit_factor = round(total_wins / total_losses, 4) if total_losses > 0 else (
        float('inf') if total_wins > 0 else 0
    )

    # ── Expectancy ──
    m.expectancy = round(
        m.avg_win_pct * m.win_rate + m.avg_loss_pct * (1 - m.win_rate), 6
    )

    # ── IC (Information Coefficient) ──
    scores = [t.score for t in trades]
    confidences = [t.confidence for t in trades]
    m.ic_score = round(_pearson_corr(scores, pnls), 4)
    m.ic_confidence = round(_pearson_corr(confidences, [abs(p) for p in pnls]), 4)

    # ── 连续统计 ──
    m.max_consecutive_wins, m.max_consecutive_losses = _consecutive_streaks(pnls)

    # ── 元信息 ──
    m.period_days = period_days_safe
    hold_times = [t.hold_time_s for t in trades if t.hold_time_s > 0]
    m.avg_hold_time_s = sum(hold_times) / len(hold_times) if hold_times else 0

    # 四舍五入
    m.win_rate = round(m.win_rate, 4)
    m.total_pnl_pct = round(m.total_pnl_pct, 4)
    m.avg_pnl_pct = round(m.avg_pnl_pct, 6)
    m.avg_win_pct = round(m.avg_win_pct, 6)
    m.avg_loss_pct = round(m.avg_loss_pct, 6)
    m.max_win_pct = round(m.max_win_pct, 4)
    m.max_loss_pct = round(m.max_loss_pct, 4)
    m.max_drawdown_pct = round(m.max_drawdown_pct, 4)

    return m


# ── 辅助函数 ──

def _std(values: list[float]) -> float:
    """计算标准差"""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _max_drawdown(pnls: list[float]) -> float:
    """
    计算最大回撤（基于累计收益曲线）。
    返回负值（如 -5.2 表示最大回撤 5.2%）。
    """
    if not pnls:
        return 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = cumulative - peak
        if dd < max_dd:
            max_dd = dd

    return max_dd


def _pearson_corr(x: list[float], y: list[float]) -> float:
    """计算 Pearson 相关系数"""
    n = len(x)
    if n < 3 or n != len(y):
        return 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

    if std_x == 0 or std_y == 0:
        return 0.0

    return cov / (std_x * std_y)


def _consecutive_streaks(pnls: list[float]) -> tuple[int, int]:
    """计算最大连胜和连败"""
    max_wins = 0
    max_losses = 0
    current_wins = 0
    current_losses = 0

    for pnl in pnls:
        if pnl > 0:
            current_wins += 1
            current_losses = 0
            max_wins = max(max_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_losses = max(max_losses, current_losses)

    return max_wins, max_losses
