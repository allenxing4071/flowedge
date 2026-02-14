"""
三层验证引擎 — 防止过拟合，确保优化结果可靠。

三层验证：
  1. OOS 回测验证：在样本外数据上回测，检查 Sharpe>0.5、MaxDD<15%
  2. 参数邻域稳定性：对最优参数做 ±10% 扰动，检查绩效不大幅衰退
  3. Bootstrap 统计检验：对交易序列重采样，计算置信区间

学术基础：
  - Efron & Tibshirani (1993): Bootstrap 方法
  - López de Prado (2018): 防过拟合回测框架
  - White (2000): Reality Check for Data Snooping
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Optional

from .backtester import SignalBacktester, BacktestConfig, BacktestResult
from .data_manager import SignalRecord
from .metrics import PerformanceMetrics, TradeResult, calculate_metrics
from .param_registry import ParamRegistry

logger = logging.getLogger("flowedge.optimizer.validator")


# ── 验证阈值（默认值） ──

@dataclass
class ValidationThresholds:
    """验证通过的最低标准"""
    # OOS 回测
    min_sharpe: float = 0.5           # 最低 Sharpe Ratio
    max_drawdown: float = 15.0        # 最大回撤上限 %
    min_trades: int = 10              # 最少交易笔数
    min_win_rate: float = 0.35        # 最低胜率
    min_profit_factor: float = 1.0    # 最低盈亏比

    # 参数邻域稳定性
    perturbation_pct: float = 10.0    # 扰动幅度 ±%
    n_perturbations: int = 20         # 扰动次数
    max_sharpe_decay: float = 0.5     # Sharpe 最大衰退比例（0.5 = 允许衰退 50%）
    min_stable_pct: float = 0.7       # 至少 70% 的扰动仍然盈利

    # Bootstrap 检验
    n_bootstrap: int = 1000           # Bootstrap 重采样次数
    confidence_level: float = 0.95    # 置信水平
    min_bootstrap_sharpe: float = 0.0 # Bootstrap 下界 Sharpe > 0


@dataclass
class LayerResult:
    """单层验证结果"""
    layer: str           # "oos" / "stability" / "bootstrap"
    passed: bool
    score: float         # 0-1 综合评分
    details: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """完整验证结果"""
    passed: bool                              # 三层全部通过
    overall_score: float                      # 0-1 综合评分
    layers: list[LayerResult] = field(default_factory=list)
    params_validated: dict[str, float] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)


class ParamValidator:
    """
    三层参数验证器。

    使用方式：
        validator = ParamValidator(registry)
        result = validator.validate(
            params=best_params,
            oos_data=test_records,
            train_metrics=train_metrics,
        )
        if result.passed:
            registry.apply_and_save(params)
    """

    def __init__(
        self,
        registry: Optional[ParamRegistry] = None,
        thresholds: Optional[ValidationThresholds] = None,
    ):
        self._registry = registry
        self._thresholds = thresholds or ValidationThresholds()
        self._backtester = SignalBacktester(registry=registry)

    def validate(
        self,
        params: dict[str, float],
        oos_data: list[SignalRecord],
        train_metrics: Optional[PerformanceMetrics] = None,
        backtest_config: Optional[BacktestConfig] = None,
    ) -> ValidationResult:
        """
        执行三层验证。

        参数:
            params: 待验证的参数集
            oos_data: 样本外数据（验证/测试集）
            train_metrics: 训练集上的绩效（用于对比）
            backtest_config: 回测配置

        返回:
            ValidationResult
        """
        config = backtest_config or BacktestConfig()
        layers: list[LayerResult] = []

        # Layer 1: OOS 回测验证
        oos_result = self._validate_oos(params, oos_data, train_metrics, config)
        layers.append(oos_result)

        # Layer 2: 参数邻域稳定性
        stability_result = self._validate_stability(params, oos_data, config)
        layers.append(stability_result)

        # Layer 3: Bootstrap 统计检验（仅在 OOS 有交易时执行）
        if oos_result.details.get("trades"):
            bootstrap_result = self._validate_bootstrap(
                oos_result.details["trades"], oos_result.details.get("period_days", 1)
            )
        else:
            bootstrap_result = LayerResult(
                layer="bootstrap",
                passed=False,
                score=0.0,
                details={"reason": "OOS 无交易，无法执行 Bootstrap"},
            )
        layers.append(bootstrap_result)

        # 综合评判
        all_passed = all(lr.passed for lr in layers)
        overall_score = sum(lr.score for lr in layers) / len(layers) if layers else 0

        recommendations = self._generate_recommendations(layers, params, train_metrics)

        return ValidationResult(
            passed=all_passed,
            overall_score=round(overall_score, 4),
            layers=layers,
            params_validated=params,
            recommendations=recommendations,
        )

    # ── Layer 1: OOS 回测验证 ──

    def _validate_oos(
        self,
        params: dict[str, float],
        oos_data: list[SignalRecord],
        train_metrics: Optional[PerformanceMetrics],
        config: BacktestConfig,
    ) -> LayerResult:
        """样本外回测验证"""
        th = self._thresholds

        if len(oos_data) < th.min_trades:
            return LayerResult(
                layer="oos",
                passed=False,
                score=0.0,
                details={"reason": f"OOS 数据不足: {len(oos_data)} < {th.min_trades}"},
            )

        result = self._backtester.run(oos_data, params=params, config=config)
        m = result.metrics
        warnings = []

        # 检查各项指标
        checks = {
            "sharpe_ok": m.sharpe_ratio >= th.min_sharpe,
            "drawdown_ok": abs(m.max_drawdown_pct) <= th.max_drawdown,
            "trades_ok": m.total_trades >= th.min_trades,
            "win_rate_ok": m.win_rate >= th.min_win_rate,
            "profit_factor_ok": m.profit_factor >= th.min_profit_factor,
        }

        # 与训练集对比（检测过拟合）
        if train_metrics and train_metrics.sharpe_ratio > 0:
            sharpe_ratio_decay = 1 - (m.sharpe_ratio / train_metrics.sharpe_ratio)
            checks["overfit_ok"] = sharpe_ratio_decay < th.max_sharpe_decay
            if sharpe_ratio_decay > 0.3:
                warnings.append(
                    f"Sharpe 衰退 {sharpe_ratio_decay:.1%}（训练 {train_metrics.sharpe_ratio:.2f} → OOS {m.sharpe_ratio:.2f}）"
                )
        else:
            checks["overfit_ok"] = True

        passed = all(checks.values())
        # 评分：通过的检查项占比
        score = sum(1 for v in checks.values() if v) / len(checks)

        details = {
            "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown_pct": m.max_drawdown_pct,
            "total_trades": m.total_trades,
            "win_rate": m.win_rate,
            "profit_factor": m.profit_factor,
            "total_pnl_pct": m.total_pnl_pct,
            "checks": checks,
            "trades": result.trades,
            "period_days": m.period_days,
        }

        if not checks.get("sharpe_ok"):
            warnings.append(f"OOS Sharpe {m.sharpe_ratio:.2f} < 阈值 {th.min_sharpe}")
        if not checks.get("drawdown_ok"):
            warnings.append(f"OOS MaxDD {m.max_drawdown_pct:.2f}% > 阈值 {th.max_drawdown}%")

        return LayerResult(
            layer="oos",
            passed=passed,
            score=round(score, 4),
            details=details,
            warnings=warnings,
        )

    # ── Layer 2: 参数邻域稳定性 ──

    def _validate_stability(
        self,
        params: dict[str, float],
        data: list[SignalRecord],
        config: BacktestConfig,
    ) -> LayerResult:
        """参数邻域稳定性检验 — 对最优参数做随机扰动，检查绩效是否稳健"""
        th = self._thresholds
        warnings = []

        base_result = self._backtester.run(data, params=params, config=config)
        base_sharpe = base_result.metrics.sharpe_ratio

        perturbed_sharpes = []
        profitable_count = 0

        for i in range(th.n_perturbations):
            perturbed = self._perturb_params(params, th.perturbation_pct, seed=i)
            p_result = self._backtester.run(data, params=perturbed, config=config)
            p_sharpe = p_result.metrics.sharpe_ratio
            perturbed_sharpes.append(p_sharpe)

            if p_result.metrics.total_pnl_pct > 0:
                profitable_count += 1

        # 评估稳定性
        if not perturbed_sharpes:
            return LayerResult(
                layer="stability",
                passed=False,
                score=0.0,
                details={"reason": "无法生成扰动参数"},
            )

        avg_perturbed_sharpe = sum(perturbed_sharpes) / len(perturbed_sharpes)
        min_perturbed_sharpe = min(perturbed_sharpes)
        max_perturbed_sharpe = max(perturbed_sharpes)
        stable_pct = profitable_count / len(perturbed_sharpes)

        # Sharpe 衰退检查
        if base_sharpe > 0:
            avg_decay = 1 - (avg_perturbed_sharpe / base_sharpe)
        else:
            avg_decay = 0

        checks = {
            "decay_ok": avg_decay < th.max_sharpe_decay,
            "stable_pct_ok": stable_pct >= th.min_stable_pct,
        }

        passed = all(checks.values())
        score = sum(1 for v in checks.values() if v) / len(checks)

        if not checks["decay_ok"]:
            warnings.append(
                f"参数扰动后 Sharpe 平均衰退 {avg_decay:.1%}（基准 {base_sharpe:.2f} → 平均 {avg_perturbed_sharpe:.2f}）"
            )
        if not checks["stable_pct_ok"]:
            warnings.append(
                f"仅 {stable_pct:.0%} 的扰动仍盈利（阈值 {th.min_stable_pct:.0%}）"
            )

        return LayerResult(
            layer="stability",
            passed=passed,
            score=round(score, 4),
            details={
                "base_sharpe": base_sharpe,
                "avg_perturbed_sharpe": round(avg_perturbed_sharpe, 4),
                "min_perturbed_sharpe": round(min_perturbed_sharpe, 4),
                "max_perturbed_sharpe": round(max_perturbed_sharpe, 4),
                "avg_decay": round(avg_decay, 4),
                "stable_pct": round(stable_pct, 4),
                "n_perturbations": th.n_perturbations,
                "checks": checks,
            },
            warnings=warnings,
        )

    # ── Layer 3: Bootstrap 统计检验 ──

    def _validate_bootstrap(
        self,
        trades: list[TradeResult],
        period_days: float,
    ) -> LayerResult:
        """Bootstrap 重采样检验 — 评估绩效的统计显著性"""
        th = self._thresholds
        warnings = []

        if len(trades) < 5:
            return LayerResult(
                layer="bootstrap",
                passed=False,
                score=0.0,
                details={"reason": f"交易笔数不足: {len(trades)} < 5"},
            )

        # Bootstrap 重采样
        bootstrap_sharpes = []
        bootstrap_pnls = []
        n = len(trades)

        for i in range(th.n_bootstrap):
            rng = random.Random(i)
            sample = [rng.choice(trades) for _ in range(n)]
            metrics = calculate_metrics(sample, period_days=period_days)
            bootstrap_sharpes.append(metrics.sharpe_ratio)
            bootstrap_pnls.append(metrics.total_pnl_pct)

        # 计算置信区间
        bootstrap_sharpes.sort()
        bootstrap_pnls.sort()

        alpha = 1 - th.confidence_level
        lower_idx = int(alpha / 2 * th.n_bootstrap)
        upper_idx = int((1 - alpha / 2) * th.n_bootstrap)

        sharpe_ci_lower = bootstrap_sharpes[lower_idx]
        sharpe_ci_upper = bootstrap_sharpes[min(upper_idx, len(bootstrap_sharpes) - 1)]
        pnl_ci_lower = bootstrap_pnls[lower_idx]
        pnl_ci_upper = bootstrap_pnls[min(upper_idx, len(bootstrap_pnls) - 1)]

        mean_sharpe = sum(bootstrap_sharpes) / len(bootstrap_sharpes)
        mean_pnl = sum(bootstrap_pnls) / len(bootstrap_pnls)

        # 检查
        checks = {
            "sharpe_lower_ok": sharpe_ci_lower >= th.min_bootstrap_sharpe,
            "pnl_lower_ok": pnl_ci_lower > 0,
        }

        passed = all(checks.values())
        score = sum(1 for v in checks.values() if v) / len(checks)

        if not checks["sharpe_lower_ok"]:
            warnings.append(
                f"Bootstrap Sharpe {th.confidence_level:.0%} 下界 = {sharpe_ci_lower:.2f}（< {th.min_bootstrap_sharpe}）"
            )
        if not checks["pnl_lower_ok"]:
            warnings.append(
                f"Bootstrap PnL {th.confidence_level:.0%} 下界 = {pnl_ci_lower:.2f}%（< 0）"
            )

        return LayerResult(
            layer="bootstrap",
            passed=passed,
            score=round(score, 4),
            details={
                "n_bootstrap": th.n_bootstrap,
                "confidence_level": th.confidence_level,
                "mean_sharpe": round(mean_sharpe, 4),
                "sharpe_ci": [round(sharpe_ci_lower, 4), round(sharpe_ci_upper, 4)],
                "mean_pnl": round(mean_pnl, 4),
                "pnl_ci": [round(pnl_ci_lower, 4), round(pnl_ci_upper, 4)],
                "checks": checks,
            },
            warnings=warnings,
        )

    # ── 辅助方法 ──

    def _perturb_params(
        self,
        params: dict[str, float],
        pct: float,
        seed: int = 0,
    ) -> dict[str, float]:
        """对参数做随机扰动（±pct%）"""
        rng = random.Random(seed)
        perturbed = {}

        for name, value in params.items():
            if value == 0:
                perturbed[name] = value
                continue
            # 在 [value * (1 - pct/100), value * (1 + pct/100)] 范围内随机
            delta = value * pct / 100
            new_val = rng.uniform(value - delta, value + delta)

            # 确保在 registry 定义的范围内
            if self._registry:
                pdef = self._registry._params.get(name)
                if pdef:
                    new_val = max(pdef.min_val, min(pdef.max_val, new_val))

            perturbed[name] = round(new_val, 6)

        return perturbed

    def _generate_recommendations(
        self,
        layers: list[LayerResult],
        params: dict[str, float],
        train_metrics: Optional[PerformanceMetrics],
    ) -> list[str]:
        """根据验证结果生成建议"""
        recs = []

        for lr in layers:
            if lr.passed:
                continue

            if lr.layer == "oos":
                checks = lr.details.get("checks", {})
                if not checks.get("sharpe_ok"):
                    recs.append("OOS Sharpe 不达标：建议增加训练数据量或降低参数空间维度")
                if not checks.get("drawdown_ok"):
                    recs.append("OOS 回撤过大：建议收紧止损或降低仓位")
                if not checks.get("overfit_ok"):
                    recs.append("训练→OOS 衰退严重：存在过拟合风险，建议使用 Walk-Forward 模式")
                if not checks.get("trades_ok"):
                    recs.append("OOS 交易笔数不足：建议放宽入场条件或积累更多数据")

            elif lr.layer == "stability":
                if not lr.details.get("checks", {}).get("decay_ok"):
                    recs.append("参数不稳定：最优参数可能是噪声拟合，建议缩小搜索范围")
                if not lr.details.get("checks", {}).get("stable_pct_ok"):
                    recs.append("参数鲁棒性差：多数扰动导致亏损，建议检查因子权重分布")

            elif lr.layer == "bootstrap":
                if not lr.details.get("checks", {}).get("sharpe_lower_ok"):
                    recs.append("Bootstrap Sharpe 下界不显著：样本量可能不足")
                if not lr.details.get("checks", {}).get("pnl_lower_ok"):
                    recs.append("Bootstrap PnL 下界为负：盈利不具统计显著性")

        if not recs:
            recs.append("三层验证全部通过，参数可安全应用到纸盘")

        return recs
