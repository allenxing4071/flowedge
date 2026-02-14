"""
Optuna 优化器封装 — 自动参数搜索引擎。

核心功能：
  1. 单目标优化：最大化 Sharpe Ratio
  2. 多目标优化：同时优化 Sharpe + 最小化 MaxDrawdown
  3. Walk-Forward 优化：滚动窗口验证，防止过拟合
  4. 参数约束：因子权重归一化、阈值逻辑一致性
  5. 剪枝：提前终止明显差的试验

技术选型：
  - TPE (Tree-structured Parzen Estimator): 默认采样器，适合中高维参数空间
  - NSGAIIISampler: 多目标优化
  - MedianPruner: 基于中位数的提前终止

学术基础：
  - Bergstra et al. (2011): TPE 算法
  - Akiba et al. (2019): Optuna 框架
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

import optuna
from optuna.samplers import TPESampler, NSGAIIISampler

from .param_registry import ParamRegistry, ParamDef
from .data_manager import DataManager, SignalRecord
from .backtester import SignalBacktester, BacktestConfig, BacktestResult
from .metrics import PerformanceMetrics

logger = logging.getLogger("flowedge.optimizer")

# 抑制 Optuna 的 INFO 日志（太多了）
optuna.logging.set_verbosity(optuna.logging.WARNING)


@dataclass
class OptimizationConfig:
    """优化配置"""
    # 优化模式
    mode: str = "single"            # "single" / "multi" / "walk_forward"
    # 试验次数
    n_trials: int = 100
    # 超时（秒）
    timeout_s: Optional[int] = None
    # 参数组（要优化哪些参数）
    param_groups: list[str] = field(default_factory=lambda: ["weights", "signal_thresholds"])
    # 排除的参数
    exclude_params: list[str] = field(default_factory=list)
    # 回测配置
    backtest_config: BacktestConfig = field(default_factory=BacktestConfig)
    # Walk-Forward 配置
    wf_train_days: int = 30
    wf_val_days: int = 7
    wf_n_splits: Optional[int] = None
    # 单目标优化目标
    objective_metric: str = "sharpe_ratio"  # sharpe_ratio / profit_factor / win_rate / total_pnl_pct
    # 多目标优化目标
    multi_objectives: list[str] = field(default_factory=lambda: ["sharpe_ratio", "max_drawdown_pct"])
    # 因子权重归一化约束
    normalize_weights: bool = True
    # 数据库路径（Optuna study 持久化）
    storage_path: Optional[str] = None


@dataclass
class OptimizationResult:
    """优化结果"""
    study_name: str
    mode: str
    n_trials_completed: int
    best_params: dict[str, float]
    best_value: float
    best_metrics: Optional[PerformanceMetrics] = None
    elapsed_s: float = 0
    # Walk-Forward 结果
    wf_results: list[dict] = field(default_factory=list)
    # 参数稳定性（标准差）
    param_stability: dict[str, float] = field(default_factory=dict)


class FlowEdgeOptimizer:
    """
    FlowEdge 参数优化器 — Optuna 封装。

    使用方式：
        optimizer = FlowEdgeOptimizer(registry, data_manager)
        result = optimizer.run(train_data, config)

    与 API 集成：
        POST /optimizer/run → 启动优化
        GET  /optimizer/status → 查看进度
        GET  /optimizer/results → 查看结果
    """

    def __init__(
        self,
        registry: ParamRegistry,
        data_manager: DataManager,
    ):
        self._registry = registry
        self._data_manager = data_manager
        self._backtester = SignalBacktester(registry=registry)
        self._current_study: Optional[optuna.Study] = None
        self._is_running = False
        self._last_result: Optional[OptimizationResult] = None
        # 防止多入口并发触发优化（API / scheduler / evolution）
        self._run_lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def last_result(self) -> Optional[OptimizationResult]:
        return self._last_result

    def run(
        self,
        records: list[SignalRecord],
        config: Optional[OptimizationConfig] = None,
    ) -> OptimizationResult:
        """
        执行参数优化。

        参数:
            records: 训练数据
            config: 优化配置

        返回:
            OptimizationResult
        """
        config = config or OptimizationConfig()
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("优化器正在运行中，请稍后重试")
        self._is_running = True
        start_time = time.time()

        try:
            if config.mode == "single":
                result = self._run_single_objective(records, config)
            elif config.mode == "multi":
                result = self._run_multi_objective(records, config)
            elif config.mode == "walk_forward":
                result = self._run_walk_forward(records, config)
            else:
                raise ValueError(f"未知优化模式: {config.mode}")

            result.elapsed_s = round(time.time() - start_time, 2)
            self._last_result = result
            return result
        finally:
            self._is_running = False
            self._run_lock.release()

    # ── 单目标优化 ──

    def _run_single_objective(
        self,
        records: list[SignalRecord],
        config: OptimizationConfig,
    ) -> OptimizationResult:
        """单目标优化（最大化指定指标）"""
        search_space = self._registry.get_search_space(
            groups=config.param_groups,
            exclude=config.exclude_params,
        )

        study_name = f"flowedge_single_{int(time.time())}"
        storage = f"sqlite:///{config.storage_path}" if config.storage_path else None

        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
            sampler=TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(n_startup_trials=10),
            storage=storage,
        )
        self._current_study = study

        def objective(trial: optuna.Trial) -> float:
            params = self._sample_params(trial, search_space, config.normalize_weights)
            result = self._backtester.run(records, params=params, config=config.backtest_config)

            # 记录额外指标（用于分析）
            trial.set_user_attr("win_rate", result.metrics.win_rate)
            trial.set_user_attr("total_pnl_pct", result.metrics.total_pnl_pct)
            trial.set_user_attr("max_drawdown_pct", result.metrics.max_drawdown_pct)
            trial.set_user_attr("profit_factor", result.metrics.profit_factor)
            trial.set_user_attr("traded_signals", result.traded_signals)

            return getattr(result.metrics, config.objective_metric, 0)

        study.optimize(objective, n_trials=config.n_trials, timeout=config.timeout_s)

        best_trial = study.best_trial
        best_params = best_trial.params

        # 用最优参数跑一次完整回测获取详细指标
        best_result = self._backtester.run(records, params=best_params, config=config.backtest_config)

        # 计算参数稳定性
        stability = self._calculate_stability(study, search_space)

        return OptimizationResult(
            study_name=study_name,
            mode="single",
            n_trials_completed=len(study.trials),
            best_params=best_params,
            best_value=best_trial.value,
            best_metrics=best_result.metrics,
            param_stability=stability,
        )

    # ── 多目标优化 ──

    def _run_multi_objective(
        self,
        records: list[SignalRecord],
        config: OptimizationConfig,
    ) -> OptimizationResult:
        """多目标优化（Pareto 前沿）"""
        search_space = self._registry.get_search_space(
            groups=config.param_groups,
            exclude=config.exclude_params,
        )

        study_name = f"flowedge_multi_{int(time.time())}"

        # 方向：Sharpe 最大化，MaxDD 最小化（取绝对值后最小化）
        directions = []
        for obj in config.multi_objectives:
            if obj in ("max_drawdown_pct",):
                directions.append("minimize")  # 回撤越小越好
            else:
                directions.append("maximize")

        study = optuna.create_study(
            study_name=study_name,
            directions=directions,
            sampler=NSGAIIISampler(seed=42),
        )
        self._current_study = study

        def objective(trial: optuna.Trial) -> tuple:
            params = self._sample_params(trial, search_space, config.normalize_weights)
            result = self._backtester.run(records, params=params, config=config.backtest_config)

            values = []
            for obj in config.multi_objectives:
                val = getattr(result.metrics, obj, 0)
                if obj == "max_drawdown_pct":
                    val = abs(val)  # 取绝对值
                values.append(val)

            trial.set_user_attr("win_rate", result.metrics.win_rate)
            trial.set_user_attr("total_pnl_pct", result.metrics.total_pnl_pct)

            return tuple(values)

        study.optimize(objective, n_trials=config.n_trials, timeout=config.timeout_s)

        # 从 Pareto 前沿选择最佳（按第一个目标排序）
        best_trials = study.best_trials
        if best_trials:
            best = max(best_trials, key=lambda t: t.values[0])
            best_params = best.params
            best_value = best.values[0]
        else:
            best_params = {}
            best_value = 0

        best_result = self._backtester.run(records, params=best_params, config=config.backtest_config)

        return OptimizationResult(
            study_name=study_name,
            mode="multi",
            n_trials_completed=len(study.trials),
            best_params=best_params,
            best_value=best_value,
            best_metrics=best_result.metrics,
        )

    # ── Walk-Forward 优化 ──

    def _run_walk_forward(
        self,
        records: list[SignalRecord],
        config: OptimizationConfig,
    ) -> OptimizationResult:
        """
        Walk-Forward 优化 — 最可靠的防过拟合方法。

        流程：
          1. 将数据分成 N 个滚动窗口（训练期 + 验证期）
          2. 每个窗口独立优化
          3. 用验证期数据评估优化结果
          4. 汇总所有窗口的验证期绩效
        """
        windows = self._data_manager.walk_forward_splits(
            records=records,
            train_days=config.wf_train_days,
            val_days=config.wf_val_days,
            n_splits=config.wf_n_splits,
        )

        if not windows:
            logger.warning("数据不足以进行 Walk-Forward 优化")
            return OptimizationResult(
                study_name="walk_forward_insufficient_data",
                mode="walk_forward",
                n_trials_completed=0,
                best_params={},
                best_value=0,
            )

        wf_results = []
        all_val_trades = []

        for window in windows:
            logger.info(
                f"Walk-Forward 窗口 {window.window_idx}: "
                f"训练 {len(window.train)} 条, 验证 {len(window.validation)} 条"
            )

            # 在训练集上优化
            train_config = OptimizationConfig(
                mode="single",
                n_trials=max(config.n_trials // len(windows), 20),
                param_groups=config.param_groups,
                exclude_params=config.exclude_params,
                backtest_config=config.backtest_config,
                objective_metric=config.objective_metric,
            )
            train_result = self._run_single_objective(window.train, train_config)

            # 在验证集上评估
            val_result = self._backtester.run(
                window.validation,
                params=train_result.best_params,
                config=config.backtest_config,
            )

            all_val_trades.extend(val_result.trades)

            wf_results.append({
                "window_idx": window.window_idx,
                "train_size": len(window.train),
                "val_size": len(window.validation),
                "train_sharpe": train_result.best_value,
                "val_sharpe": val_result.metrics.sharpe_ratio,
                "val_win_rate": val_result.metrics.win_rate,
                "val_pnl": val_result.metrics.total_pnl_pct,
                "best_params": train_result.best_params,
            })

        # 汇总验证期绩效
        from .metrics import calculate_metrics
        total_val_metrics = calculate_metrics(all_val_trades)

        # 使用最后一个窗口的最优参数作为推荐参数
        best_params = wf_results[-1]["best_params"] if wf_results else {}

        return OptimizationResult(
            study_name=f"walk_forward_{int(time.time())}",
            mode="walk_forward",
            n_trials_completed=sum(1 for _ in wf_results),
            best_params=best_params,
            best_value=total_val_metrics.sharpe_ratio,
            best_metrics=total_val_metrics,
            wf_results=wf_results,
        )

    # ── 辅助方法 ──

    def _sample_params(
        self,
        trial: optuna.Trial,
        search_space: dict[str, ParamDef],
        normalize_weights: bool = True,
    ) -> dict[str, float]:
        """从 Optuna trial 采样参数"""
        params = {}
        weight_params = {}

        for name, pdef in search_space.items():
            if pdef.param_type == "int":
                value = trial.suggest_int(name, int(pdef.min_val), int(pdef.max_val))
            elif pdef.step:
                value = trial.suggest_float(name, pdef.min_val, pdef.max_val, step=pdef.step)
            else:
                value = trial.suggest_float(name, pdef.min_val, pdef.max_val)

            params[name] = value

            if pdef.group == "weights":
                weight_params[name] = value

        # 因子权重归一化（保证总和 = 1.0）
        if normalize_weights and weight_params:
            total = sum(weight_params.values())
            if total > 0:
                for name in weight_params:
                    params[name] = weight_params[name] / total

        return params

    def _calculate_stability(
        self,
        study: optuna.Study,
        search_space: dict[str, ParamDef],
    ) -> dict[str, float]:
        """
        计算参数稳定性（Top 20% 试验的参数标准差）。
        标准差越小 → 参数越稳定 → 过拟合风险越低。
        """
        trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if len(trials) < 5:
            return {}

        # 取 Top 20% 的试验
        trials.sort(key=lambda t: t.value or 0, reverse=True)
        top_n = max(3, len(trials) // 5)
        top_trials = trials[:top_n]

        stability = {}
        for name in search_space:
            values = [t.params.get(name, 0) for t in top_trials if name in t.params]
            if len(values) >= 2:
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
                import math
                stability[name] = round(math.sqrt(variance), 6)

        return stability

    def get_status(self) -> dict:
        """获取当前优化状态"""
        status = {
            "is_running": self._is_running,
            "has_result": self._last_result is not None,
        }

        if self._current_study:
            completed = len([
                t for t in self._current_study.trials
                if t.state == optuna.trial.TrialState.COMPLETE
            ])
            status["trials_completed"] = completed

        if self._last_result:
            status["last_result"] = {
                "study_name": self._last_result.study_name,
                "mode": self._last_result.mode,
                "n_trials": self._last_result.n_trials_completed,
                "best_value": self._last_result.best_value,
                "elapsed_s": self._last_result.elapsed_s,
            }

        return status
