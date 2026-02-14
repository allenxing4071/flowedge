"""
持续进化循环引擎 — 编排"数据积累→优化→验证→应用→监控"完整闭环。

核心理念：
  系统不是一次性优化，而是持续自我进化。每个周期：
  1. 数据积累：收集新的信号和交易数据
  2. 质量检查：确认数据量和质量满足优化要求
  3. 参数优化：Optuna 搜索最优参数
  4. 三层验证：OOS/稳定性/Bootstrap
  5. A/B 对照：新参数 vs 当前参数
  6. AI 评估：生成评估报告
  7. 决策应用：通过验证 → 自动/手动应用
  8. 效果监控：跟踪应用后的实际表现

进化记录：
  每轮进化的完整记录（参数、指标、验证、评估）持久化存储，
  形成系统的"进化史"，支持回溯和趋势分析。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .ab_test import ABTester, ABGroup
from .ai_evaluator import AIEvaluator
from .backtester import BacktestConfig
from .data_manager import DataManager
from .optimizer import FlowEdgeOptimizer, OptimizationConfig
from .param_registry import ParamRegistry
from .validator import ParamValidator

logger = logging.getLogger("flowedge.optimizer.evolution")


@dataclass
class EvolutionConfig:
    """进化循环配置"""
    # 数据要求
    min_signals: int = 30               # 最少信号数
    min_new_signals: int = 10           # 最少新增信号数（相比上次进化）
    # 优化
    n_trials: int = 100
    param_groups: list[str] = field(
        default_factory=lambda: ["weights", "signal_thresholds", "gate"]
    )
    # A/B 对照
    run_ab_test: bool = True            # 是否执行 A/B 对照
    # AI 评估
    run_ai_eval: bool = True            # 是否执行 AI 评估
    min_grade: str = "C"                # 最低通过评级
    # 应用策略
    auto_apply: bool = False            # 自动应用（默认需人工确认）
    # 持久化
    history_dir: str = "data/optimizer/evolution"


@dataclass
class EvolutionCycle:
    """单次进化循环记录"""
    cycle_id: str
    started_at: str
    finished_at: Optional[str] = None
    status: str = "running"             # running / success / failed / skipped / pending_approval

    # 数据阶段
    total_signals: int = 0
    new_signals: int = 0

    # 优化阶段
    optimization: Optional[dict] = None

    # 验证阶段
    validation_passed: bool = False
    validation_score: float = 0.0

    # A/B 对照阶段
    ab_test: Optional[dict] = None

    # AI 评估阶段
    ai_grade: str = ""
    ai_score: float = 0.0
    ai_summary: str = ""

    # 应用阶段
    applied: bool = False
    snapshot_name: Optional[str] = None

    # 元信息
    elapsed_s: float = 0
    failure_reason: Optional[str] = None
    best_params: Optional[dict] = None


class EvolutionEngine:
    """
    持续进化循环引擎。

    使用方式：
        engine = EvolutionEngine(registry, data_manager, optimizer)
        cycle = engine.evolve()  # 执行一轮进化

    查看进化史：
        history = engine.get_history()
    """

    # 评级排序（用于比较）
    GRADE_ORDER = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}

    def __init__(
        self,
        registry: ParamRegistry,
        data_manager: DataManager,
        optimizer: FlowEdgeOptimizer,
        config: Optional[EvolutionConfig] = None,
        paper_trader=None,
    ):
        self._registry = registry
        self._data_manager = data_manager
        self._optimizer = optimizer
        self._validator = ParamValidator(registry=registry)
        self._ab_tester = ABTester(registry=registry)
        self._evaluator = AIEvaluator()
        self._config = config or EvolutionConfig()
        self._cycles: list[EvolutionCycle] = []
        self._paper_trader = paper_trader  # 纸盘引用，参数迭代后自动重置

        # 确保持久化目录存在
        Path(self._config.history_dir).mkdir(parents=True, exist_ok=True)

        # 加载历史
        self._load_history()

    def evolve(self, config: Optional[EvolutionConfig] = None) -> EvolutionCycle:
        """执行一轮完整进化"""
        cfg = config or self._config
        cycle_id = f"evo_{int(time.time())}"
        cycle = EvolutionCycle(
            cycle_id=cycle_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        start = time.time()

        try:
            # ── Step 1: 数据检查 ──
            records = self._data_manager.load_all(require_1h=True)
            cycle.total_signals = len(records)

            if len(records) < cfg.min_signals:
                cycle.status = "skipped"
                cycle.failure_reason = f"信号不足: {len(records)} < {cfg.min_signals}"
                logger.warning(f"[Evolution] {cycle.failure_reason}")
                return cycle

            # 检查新增信号数
            last_total = self._cycles[-1].total_signals if self._cycles else 0
            cycle.new_signals = max(0, len(records) - last_total)

            if cycle.new_signals < cfg.min_new_signals and self._cycles:
                cycle.status = "skipped"
                cycle.failure_reason = f"新增信号不足: {cycle.new_signals} < {cfg.min_new_signals}"
                logger.warning(f"[Evolution] {cycle.failure_reason}")
                return cycle

            # ── Step 2: 数据分割 ──
            split = self._data_manager.time_split(records, train_pct=0.7, val_pct=0, test_pct=0.3)
            train_data = split.train
            test_data = split.test

            # ── Step 3: 参数优化 ──
            logger.info(f"[Evolution] 开始优化: {len(train_data)} 训练 / {len(test_data)} 测试")

            opt_config = OptimizationConfig(
                mode="single",
                n_trials=cfg.n_trials,
                param_groups=cfg.param_groups,
                backtest_config=BacktestConfig(),
            )
            try:
                opt_result = self._optimizer.run(train_data, opt_config)
            except RuntimeError as e:
                cycle.status = "skipped"
                cycle.failure_reason = str(e)
                logger.warning(f"[Evolution] {cycle.failure_reason}")
                return cycle

            cycle.optimization = {
                "study_name": opt_result.study_name,
                "n_trials": opt_result.n_trials_completed,
                "best_value": opt_result.best_value,
                "elapsed_s": opt_result.elapsed_s,
            }
            cycle.best_params = opt_result.best_params

            if not opt_result.best_params:
                cycle.status = "failed"
                cycle.failure_reason = "优化未产出有效参数"
                return cycle

            # ── Step 4: 三层验证 ──
            logger.info(f"[Evolution] 开始验证...")

            val_result = self._validator.validate(
                params=opt_result.best_params,
                oos_data=test_data,
                train_metrics=opt_result.best_metrics,
            )
            cycle.validation_passed = val_result.passed
            cycle.validation_score = val_result.overall_score

            if not val_result.passed:
                cycle.status = "failed"
                cycle.failure_reason = "验证未通过: " + "; ".join(
                    w for lr in val_result.layers for w in lr.warnings
                )
                logger.warning(f"[Evolution] {cycle.failure_reason}")
                # 继续执行评估（即使验证失败也生成报告）

            # ── Step 5: A/B 对照 ──
            if cfg.run_ab_test and val_result.passed:
                logger.info(f"[Evolution] 执行 A/B 对照...")
                current_params = self._registry.get_all()
                ab_result = self._ab_tester.run(
                    data=test_data,
                    groups=[
                        ABGroup("current", current_params, "当前参数"),
                        ABGroup("optimized", opt_result.best_params, "优化参数"),
                    ],
                )
                cycle.ab_test = {
                    "best_group": ab_result.best_group,
                    "summary": ab_result.summary,
                    "significant": any(c.significant for c in ab_result.comparisons),
                }

            # ── Step 6: AI 评估 ──
            if cfg.run_ai_eval:
                logger.info(f"[Evolution] AI 评估...")
                # 获取 OOS 指标
                from .backtester import SignalBacktester
                bt = SignalBacktester(registry=self._registry)
                oos_result = bt.run(test_data, params=opt_result.best_params)

                report = self._evaluator.evaluate(
                    params=opt_result.best_params,
                    train_metrics=opt_result.best_metrics,
                    oos_metrics=oos_result.metrics,
                    validation=val_result,
                    param_stability=opt_result.param_stability,
                )
                cycle.ai_grade = report.overall_grade
                cycle.ai_score = report.overall_score
                cycle.ai_summary = report.summary

            # ── Step 7: 决策应用 ──
            if cfg.run_ai_eval:
                grade_ok = self.GRADE_ORDER.get(cycle.ai_grade, 0) >= self.GRADE_ORDER.get(cfg.min_grade, 0)
            else:
                # 未启用 AI 评估时，仅依赖验证结果做应用决策
                grade_ok = True
                cycle.ai_grade = "N/A"
                cycle.ai_score = 0.0
                cycle.ai_summary = "未启用 AI 评估，按验证结果决策"

            if val_result.passed and grade_ok:
                if cfg.auto_apply:
                    snapshot = self._registry.apply_and_save(
                        opt_result.best_params,
                        label=f"evolution_{cycle_id}",
                    )
                    cycle.applied = True
                    cycle.snapshot_name = snapshot
                    cycle.status = "success"
                    # 参数迭代后重置纸盘：新参数需要从零开始验证效果
                    self._reset_paper_trader(cycle_id)
                    logger.info(f"[Evolution] 进化成功，已自动应用参数并重置纸盘")
                else:
                    cycle.status = "pending_approval"
                    logger.info(f"[Evolution] 进化完成，等待人工确认应用")
            elif val_result.passed:
                cycle.status = "failed"
                cycle.failure_reason = f"AI 评级 {cycle.ai_grade} 低于要求 {cfg.min_grade}"
            # else: 验证失败已在上面处理

        except Exception as e:
            cycle.status = "failed"
            cycle.failure_reason = f"异常: {str(e)}"
            logger.error(f"[Evolution] 进化异常: {e}", exc_info=True)

        finally:
            cycle.elapsed_s = round(time.time() - start, 2)
            cycle.finished_at = datetime.now(timezone.utc).isoformat()
            self._cycles.append(cycle)
            self._save_cycle(cycle)

        return cycle

    def approve_and_apply(self, cycle_id: str) -> dict:
        """人工确认并应用指定进化周期的参数"""
        cycle = next((c for c in self._cycles if c.cycle_id == cycle_id), None)
        if not cycle:
            return {"error": f"未找到进化周期: {cycle_id}"}

        if cycle.status != "pending_approval":
            return {"error": f"该周期状态为 {cycle.status}，不可应用"}

        if not cycle.best_params:
            return {"error": "该周期无最优参数"}

        snapshot = self._registry.apply_and_save(
            cycle.best_params,
            label=f"evolution_{cycle_id}_approved",
        )
        cycle.applied = True
        cycle.snapshot_name = snapshot
        cycle.status = "success"
        self._save_cycle(cycle)

        # 参数迭代后重置纸盘：新参数需要从零开始验证效果
        self._reset_paper_trader(cycle_id)

        return {
            "success": True,
            "snapshot": snapshot,
            "message": f"已应用进化周期 {cycle_id} 的参数，纸盘已重置",
        }

    def get_status(self) -> dict:
        """获取进化引擎状态"""
        return {
            "total_cycles": len(self._cycles),
            "successful": sum(1 for c in self._cycles if c.status == "success"),
            "failed": sum(1 for c in self._cycles if c.status == "failed"),
            "skipped": sum(1 for c in self._cycles if c.status == "skipped"),
            "pending": sum(1 for c in self._cycles if c.status == "pending_approval"),
            "config": {
                "min_signals": self._config.min_signals,
                "min_new_signals": self._config.min_new_signals,
                "n_trials": self._config.n_trials,
                "auto_apply": self._config.auto_apply,
                "min_grade": self._config.min_grade,
            },
        }

    def get_history(self, limit: int = 10) -> list[dict]:
        """获取进化历史"""
        cycles = self._cycles[-limit:]
        return [
            {
                "cycle_id": c.cycle_id,
                "status": c.status,
                "started_at": c.started_at,
                "elapsed_s": c.elapsed_s,
                "total_signals": c.total_signals,
                "new_signals": c.new_signals,
                "validation_passed": c.validation_passed,
                "validation_score": c.validation_score,
                "ai_grade": c.ai_grade,
                "ai_score": c.ai_score,
                "ai_summary": c.ai_summary,
                "applied": c.applied,
                "failure_reason": c.failure_reason,
            }
            for c in reversed(cycles)
        ]

    def get_evolution_trend(self) -> dict:
        """获取进化趋势（各周期的关键指标变化）"""
        successful = [c for c in self._cycles if c.status == "success" and c.optimization]

        if not successful:
            return {"message": "暂无成功的进化记录", "trend": []}

        trend = []
        for c in successful:
            trend.append({
                "cycle_id": c.cycle_id,
                "started_at": c.started_at,
                "best_sharpe": c.optimization.get("best_value", 0) if c.optimization else 0,
                "validation_score": c.validation_score,
                "ai_grade": c.ai_grade,
                "ai_score": c.ai_score,
                "total_signals": c.total_signals,
            })

        return {"trend": trend, "total_successful": len(successful)}

    # ── 纸盘重置 ──

    def _reset_paper_trader(self, cycle_id: str):
        """参数迭代后重置纸盘，让新参数从零开始验证效果"""
        if self._paper_trader:
            try:
                self._paper_trader.reset()
                logger.info(f"[Evolution] 纸盘已重置（进化周期 {cycle_id}）")
            except Exception as e:
                logger.warning(f"[Evolution] 纸盘重置失败: {e}")

    # ── 持久化 ──

    def _save_cycle(self, cycle: EvolutionCycle):
        """保存进化记录到文件"""
        path = Path(self._config.history_dir) / f"{cycle.cycle_id}.json"
        data = {
            "cycle_id": cycle.cycle_id,
            "status": cycle.status,
            "started_at": cycle.started_at,
            "finished_at": cycle.finished_at,
            "elapsed_s": cycle.elapsed_s,
            "total_signals": cycle.total_signals,
            "new_signals": cycle.new_signals,
            "optimization": cycle.optimization,
            "validation_passed": cycle.validation_passed,
            "validation_score": cycle.validation_score,
            "ab_test": cycle.ab_test,
            "ai_grade": cycle.ai_grade,
            "ai_score": cycle.ai_score,
            "ai_summary": cycle.ai_summary,
            "applied": cycle.applied,
            "snapshot_name": cycle.snapshot_name,
            "failure_reason": cycle.failure_reason,
            "best_params": cycle.best_params,
        }
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"保存进化记录失败: {e}")

    def _load_history(self):
        """从文件加载历史进化记录"""
        history_dir = Path(self._config.history_dir)
        if not history_dir.exists():
            return

        for path in sorted(history_dir.glob("evo_*.json")):
            try:
                data = json.loads(path.read_text())
                cycle = EvolutionCycle(
                    cycle_id=data["cycle_id"],
                    started_at=data["started_at"],
                    finished_at=data.get("finished_at"),
                    status=data.get("status", "unknown"),
                    total_signals=data.get("total_signals", 0),
                    new_signals=data.get("new_signals", 0),
                    optimization=data.get("optimization"),
                    validation_passed=data.get("validation_passed", False),
                    validation_score=data.get("validation_score", 0),
                    ab_test=data.get("ab_test"),
                    ai_grade=data.get("ai_grade", ""),
                    ai_score=data.get("ai_score", 0),
                    ai_summary=data.get("ai_summary", ""),
                    applied=data.get("applied", False),
                    snapshot_name=data.get("snapshot_name"),
                    failure_reason=data.get("failure_reason"),
                    best_params=data.get("best_params"),
                    elapsed_s=data.get("elapsed_s", 0),
                )
                self._cycles.append(cycle)
            except Exception as e:
                logger.warning(f"加载进化记录失败 {path}: {e}")

        if self._cycles:
            logger.info(f"[Evolution] 已加载 {len(self._cycles)} 条历史进化记录")
