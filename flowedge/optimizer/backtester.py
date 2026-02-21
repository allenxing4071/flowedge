"""
信号级回测引擎 — 基于历史信号数据模拟交易。

核心逻辑：
  1. 接收一组参数（权重、阈值等）
  2. 对历史信号数据重新评估：用新权重重算 score，用新阈值重新分类信号
  3. 模拟交易执行（含滑点、手续费）
  4. 计算绩效指标

与传统 K 线回测的区别：
  - 不需要原始 K 线数据，直接使用 signal_tracker.db 中已有的信号记录
  - 每条记录已包含 score、confidence、entry_price、以及 5m/15m/1h 后的价格
  - 回测逻辑：用新参数重算 score → 判断是否入场 → 用历史价格计算盈亏

局限性（诚实标注）：
  - 无法模拟订单簿深度变化（信号级回测的固有限制）
  - 假设滑点固定（实际滑点与市场深度相关）
  - 5m/15m/1h 的价格是固定时间点快照，不是最优出场点
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .data_manager import SignalRecord
from .metrics import TradeResult, PerformanceMetrics, calculate_metrics
from .param_registry import ParamRegistry

logger = logging.getLogger("flowedge.optimizer.backtester")


@dataclass
class BacktestConfig:
    """回测配置"""
    # 交易参数
    slippage_pct: float = 0.02      # 模拟滑点 %
    fee_pct: float = 0.02           # 单边手续费 %
    # 出场时间窗口
    exit_window: str = "1h"         # "5m" / "15m" / "1h" — 用哪个时间窗口的价格作为出场
    # 过滤条件
    min_score: float = 0.15         # 最小 |score| 才入场
    min_confidence: float = 0.30    # 最小 confidence 才入场
    # 止损止盈（基于百分比）
    use_stop_loss: bool = True
    stop_loss_pct: float = 2.0      # 止损 %
    use_take_profit: bool = True
    take_profit_pct: float = 1.5    # 止盈 %


@dataclass
class BacktestResult:
    """回测结果"""
    metrics: PerformanceMetrics
    trades: list[TradeResult]
    config: BacktestConfig
    params_used: dict[str, float]
    total_signals: int = 0       # 总信号数
    filtered_signals: int = 0    # 被过滤掉的信号数
    traded_signals: int = 0      # 实际交易的信号数


class SignalBacktester:
    """
    信号级回测引擎。

    使用方式：
        bt = SignalBacktester()
        result = bt.run(records, params={"weight_cvd": 0.15, ...})

    与 Optuna 集成：
        def objective(trial):
            params = {name: trial.suggest_float(...) for name, pdef in space.items()}
            result = bt.run(train_data, params=params)
            return result.metrics.sharpe_ratio
    """

    def __init__(self, registry: Optional[ParamRegistry] = None):
        self._registry = registry

    def run(
        self,
        records: list[SignalRecord],
        params: Optional[dict[str, float]] = None,
        config: Optional[BacktestConfig] = None,
    ) -> BacktestResult:
        """
        执行回测。

        参数:
            records: 信号记录列表（来自 DataManager）
            params: 参数覆盖（覆盖 registry 中的值）
            config: 回测配置

        返回:
            BacktestResult
        """
        config = config or BacktestConfig()

        # 获取参数（优先使用传入的 params，否则从 registry 读取）
        if params:
            effective_params = params
        elif self._registry:
            effective_params = self._registry.get_all()
        else:
            effective_params = {}

        # 提取关键参数
        weights = self._extract_weights(effective_params)
        min_score = effective_params.get("gate_min_score", config.min_score)
        min_confidence = effective_params.get("gate_min_confidence", config.min_confidence)
        stop_loss_pct = effective_params.get("paper_stop_loss_pct", config.stop_loss_pct)
        take_profit_pct = effective_params.get("paper_take_profit_pct", config.take_profit_pct)

        # 信号阈值
        thresh_buy = effective_params.get("thresh_buy", 0.15)
        thresh_sell = effective_params.get("thresh_sell", -0.15)

        trades: list[TradeResult] = []
        filtered = 0

        for rec in records:
            # 跳过没有出场价格的记录
            exit_price = self._get_exit_price(rec, config.exit_window)
            if exit_price is None or exit_price <= 0:
                continue

            # 用新权重重算 score（如果有因子明细）
            if rec.factor_scores and weights:
                new_score = self._recalculate_score(rec.factor_scores, weights)
            else:
                # 无因子明细，使用原始 score
                new_score = rec.score

            # 入场过滤
            if abs(new_score) < min_score:
                filtered += 1
                continue
            if rec.confidence < min_confidence:
                filtered += 1
                continue

            # 判断方向
            if new_score >= thresh_buy:
                side = "LONG"
            elif new_score <= thresh_sell:
                side = "SHORT"
            else:
                filtered += 1
                continue

            # 计算盈亏
            pnl_pct = self._calculate_pnl(
                side=side,
                entry_price=rec.entry_price,
                exit_price=exit_price,
                slippage_pct=config.slippage_pct,
                fee_pct=config.fee_pct,
                stop_loss_pct=stop_loss_pct if config.use_stop_loss else None,
                take_profit_pct=take_profit_pct if config.use_take_profit else None,
            )

            trades.append(TradeResult(
                pnl_pct=pnl_pct,
                signal=rec.signal,
                score=new_score,
                confidence=rec.confidence,
                entry_price=rec.entry_price,
                exit_price=exit_price,
            ))

        # 计算绩效
        period_days = 0
        if records:
            time_range_ms = records[-1].entry_time_ms - records[0].entry_time_ms
            # 小时间窗会放大年化指标，最小按 1 天处理
            period_days = max(time_range_ms / (86400 * 1000), 1.0) if time_range_ms > 0 else 1.0

        metrics = calculate_metrics(trades, period_days=period_days)

        return BacktestResult(
            metrics=metrics,
            trades=trades,
            config=config,
            params_used=effective_params,
            total_signals=len(records),
            filtered_signals=filtered,
            traded_signals=len(trades),
        )

    def _extract_weights(self, params: dict[str, float]) -> dict[str, float]:
        """从参数字典中提取因子权重"""
        weights = {}
        for key, value in params.items():
            if key.startswith("weight_"):
                factor_name = key.replace("weight_", "")
                weights[factor_name] = value
        return weights

    def _recalculate_score(
        self,
        factor_scores: dict[str, float],
        weights: dict[str, float],
    ) -> float:
        """用新权重重算综合得分"""
        total_weight = sum(weights.get(name, 0) for name in factor_scores)
        if total_weight <= 0:
            return 0.0

        # 归一化权重
        norm_weights = {
            name: weights.get(name, 0) / total_weight
            for name in factor_scores
        }

        score = sum(
            factor_scores[name] * norm_weights.get(name, 0)
            for name in factor_scores
        )
        return round(score, 6)

    def _get_exit_price(self, rec: SignalRecord, window: str) -> Optional[float]:
        """获取指定时间窗口的出场价格"""
        if window == "5m":
            return rec.price_5m
        elif window == "15m":
            return rec.price_15m
        elif window == "1h":
            return rec.price_1h
        return rec.price_1h  # 默认 1h

    def _calculate_pnl(
        self,
        side: str,
        entry_price: float,
        exit_price: float,
        slippage_pct: float,
        fee_pct: float,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
    ) -> float:
        """
        计算单笔交易盈亏（含滑点和手续费）。

        注意：这是简化模型。实际交易中：
          - 滑点与市场深度相关
          - 止损可能滑点更大（极端行情）
          - 手续费可能有阶梯折扣
        """
        if entry_price <= 0:
            return 0.0

        # 应用滑点
        if side == "LONG":
            effective_entry = entry_price * (1 + slippage_pct / 100)
            effective_exit = exit_price * (1 - slippage_pct / 100)
            raw_pnl_pct = (effective_exit - effective_entry) / effective_entry * 100
        else:  # SHORT
            effective_entry = entry_price * (1 - slippage_pct / 100)
            effective_exit = exit_price * (1 + slippage_pct / 100)
            raw_pnl_pct = (effective_entry - effective_exit) / effective_entry * 100

        # 应用止损止盈
        if stop_loss_pct is not None and raw_pnl_pct < -stop_loss_pct:
            raw_pnl_pct = -stop_loss_pct
        if take_profit_pct is not None and raw_pnl_pct > take_profit_pct:
            raw_pnl_pct = take_profit_pct

        # 扣除双边手续费
        net_pnl_pct = raw_pnl_pct - 2 * fee_pct

        return round(net_pnl_pct, 6)
