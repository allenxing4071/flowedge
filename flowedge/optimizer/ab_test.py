"""
A/B 对照测试 — 多参数组并行评估，统计检验选出更优方案。

核心功能：
  1. 定义多个参数组（A/B/C...）
  2. 在同一数据集上并行回测
  3. Bootstrap 统计检验对比绩效差异
  4. 输出推荐方案（p < 0.05 才建议切换）

使用场景：
  - 优化器产出新参数 vs 当前参数
  - 不同优化策略的对比
  - 不同参数组在不同市场环境下的表现
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Optional

from .backtester import SignalBacktester, BacktestConfig, BacktestResult
from .data_manager import SignalRecord
from .metrics import PerformanceMetrics, TradeResult, calculate_metrics
from .param_registry import ParamRegistry

logger = logging.getLogger("flowedge.optimizer.ab_test")


@dataclass
class ABGroup:
    """A/B 测试的一个参数组"""
    name: str                           # 组名（如 "A_current", "B_optimized"）
    params: dict[str, float]            # 参数集
    description: str = ""               # 描述


@dataclass
class ABGroupResult:
    """单个参数组的测试结果"""
    name: str
    metrics: PerformanceMetrics
    trades: list[TradeResult]
    total_signals: int = 0
    traded_signals: int = 0


@dataclass
class ABComparisonResult:
    """两个参数组的对比结果"""
    group_a: str
    group_b: str
    # 绩效差异
    sharpe_diff: float = 0.0            # B - A
    pnl_diff: float = 0.0              # B - A
    win_rate_diff: float = 0.0         # B - A
    # Bootstrap 统计检验
    p_value_sharpe: float = 1.0        # Sharpe 差异的 p 值
    p_value_pnl: float = 1.0          # PnL 差异的 p 值
    # 结论
    significant: bool = False           # p < 0.05
    recommendation: str = ""            # 推荐结论


@dataclass
class ABTestResult:
    """完整 A/B 测试结果"""
    groups: list[ABGroupResult]
    comparisons: list[ABComparisonResult]
    best_group: str                     # 推荐的最优组
    summary: str                        # 总结文本


class ABTester:
    """
    A/B 对照测试器。

    使用方式：
        tester = ABTester(registry)
        result = tester.run(
            data=records,
            groups=[
                ABGroup("current", current_params),
                ABGroup("optimized", new_params),
            ],
        )
    """

    def __init__(
        self,
        registry: Optional[ParamRegistry] = None,
        n_bootstrap: int = 1000,
        significance_level: float = 0.05,
    ):
        self._registry = registry
        self._backtester = SignalBacktester(registry=registry)
        self._n_bootstrap = n_bootstrap
        self._significance_level = significance_level

    def run(
        self,
        data: list[SignalRecord],
        groups: list[ABGroup],
        config: Optional[BacktestConfig] = None,
    ) -> ABTestResult:
        """
        执行 A/B 对照测试。

        参数:
            data: 测试数据（同一数据集）
            groups: 参数组列表（≥2 组）
            config: 回测配置

        返回:
            ABTestResult
        """
        config = config or BacktestConfig()

        if len(groups) < 2:
            raise ValueError("A/B 测试至少需要 2 个参数组")

        # Step 1: 对每个参数组执行回测
        group_results: list[ABGroupResult] = []
        for group in groups:
            bt_result = self._backtester.run(data, params=group.params, config=config)
            group_results.append(ABGroupResult(
                name=group.name,
                metrics=bt_result.metrics,
                trades=bt_result.trades,
                total_signals=bt_result.total_signals,
                traded_signals=bt_result.traded_signals,
            ))

        # Step 2: 两两对比
        comparisons: list[ABComparisonResult] = []
        for i in range(len(group_results)):
            for j in range(i + 1, len(group_results)):
                comp = self._compare_groups(group_results[i], group_results[j])
                comparisons.append(comp)

        # Step 3: 选出最优组
        best = max(group_results, key=lambda g: g.metrics.sharpe_ratio)

        # Step 4: 生成总结
        summary = self._generate_summary(group_results, comparisons, best.name)

        return ABTestResult(
            groups=group_results,
            comparisons=comparisons,
            best_group=best.name,
            summary=summary,
        )

    def _compare_groups(
        self,
        a: ABGroupResult,
        b: ABGroupResult,
    ) -> ABComparisonResult:
        """对比两个参数组"""
        comp = ABComparisonResult(
            group_a=a.name,
            group_b=b.name,
            sharpe_diff=round(b.metrics.sharpe_ratio - a.metrics.sharpe_ratio, 4),
            pnl_diff=round(b.metrics.total_pnl_pct - a.metrics.total_pnl_pct, 4),
            win_rate_diff=round(b.metrics.win_rate - a.metrics.win_rate, 4),
        )

        # Bootstrap 统计检验
        if a.trades and b.trades:
            comp.p_value_sharpe = self._bootstrap_test_sharpe(a.trades, b.trades)
            comp.p_value_pnl = self._bootstrap_test_pnl(a.trades, b.trades)
            comp.significant = (
                comp.p_value_sharpe < self._significance_level
                or comp.p_value_pnl < self._significance_level
            )

        # 生成推荐
        if comp.significant:
            if comp.sharpe_diff > 0:
                comp.recommendation = f"建议切换到 {b.name}（Sharpe 显著更优，p={comp.p_value_sharpe:.4f}）"
            else:
                comp.recommendation = f"建议保留 {a.name}（Sharpe 显著更优，p={comp.p_value_sharpe:.4f}）"
        else:
            comp.recommendation = f"差异不显著（p={comp.p_value_sharpe:.4f}），建议继续积累数据"

        return comp

    def _bootstrap_test_sharpe(
        self,
        trades_a: list[TradeResult],
        trades_b: list[TradeResult],
    ) -> float:
        """
        Bootstrap 检验两组 Sharpe 差异的显著性。
        返回 p 值（< 0.05 表示显著）。
        """
        pnls_a = [t.pnl_pct for t in trades_a]
        pnls_b = [t.pnl_pct for t in trades_b]

        observed_diff = _mean(pnls_b) / max(_std(pnls_b), 0.001) - _mean(pnls_a) / max(_std(pnls_a), 0.001)

        # 合并数据做置换检验
        combined = pnls_a + pnls_b
        n_a = len(pnls_a)
        count_extreme = 0

        for i in range(self._n_bootstrap):
            rng = random.Random(i)
            rng.shuffle(combined)
            boot_a = combined[:n_a]
            boot_b = combined[n_a:]

            boot_diff = _mean(boot_b) / max(_std(boot_b), 0.001) - _mean(boot_a) / max(_std(boot_a), 0.001)

            if abs(boot_diff) >= abs(observed_diff):
                count_extreme += 1

        return count_extreme / self._n_bootstrap

    def _bootstrap_test_pnl(
        self,
        trades_a: list[TradeResult],
        trades_b: list[TradeResult],
    ) -> float:
        """Bootstrap 检验两组 PnL 差异的显著性"""
        pnls_a = [t.pnl_pct for t in trades_a]
        pnls_b = [t.pnl_pct for t in trades_b]

        observed_diff = _mean(pnls_b) - _mean(pnls_a)

        combined = pnls_a + pnls_b
        n_a = len(pnls_a)
        count_extreme = 0

        for i in range(self._n_bootstrap):
            rng = random.Random(i)
            rng.shuffle(combined)
            boot_a = combined[:n_a]
            boot_b = combined[n_a:]

            boot_diff = _mean(boot_b) - _mean(boot_a)
            if abs(boot_diff) >= abs(observed_diff):
                count_extreme += 1

        return count_extreme / self._n_bootstrap

    def _generate_summary(
        self,
        groups: list[ABGroupResult],
        comparisons: list[ABComparisonResult],
        best_name: str,
    ) -> str:
        """生成 A/B 测试总结"""
        lines = [f"A/B 测试完成，共 {len(groups)} 组参数："]

        for g in groups:
            m = g.metrics
            lines.append(
                f"  [{g.name}] Sharpe={m.sharpe_ratio:.2f}, "
                f"PnL={m.total_pnl_pct:.2f}%, "
                f"WinRate={m.win_rate:.1%}, "
                f"Trades={m.total_trades}"
            )

        lines.append("")
        for c in comparisons:
            lines.append(f"  {c.group_a} vs {c.group_b}: {c.recommendation}")

        lines.append(f"\n推荐: {best_name}")
        return "\n".join(lines)


# ── 辅助函数 ──

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5
