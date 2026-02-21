"""
自动优化调度器 — 样本驱动的"优化→验证→应用"闭环。

核心流程（样本达标时自动触发）：
  1. 每 5 分钟检查一次已验证信号数量（零成本 count 查询）
  2. 首轮：总样本 ≥ min_samples 时触发
  3. 后续轮：上一轮完成后，需积累 ≥ min_new_signals 条新信号才触发下一轮
  4. 执行 Walk-Forward 优化（或单目标优化）
  5. 三层验证（OOS/稳定性/Bootstrap）
  6. 验证通过 → 自动应用到 registry + 创建快照
  7. 验证失败 → 保留当前参数 + 记录失败原因

调度策略：
  - 检查间隔：5 分钟（轻量 count 查询，不消耗资源）
  - 触发条件：样本量达标（事件驱动，不浪费时间等待）
  - 冷却机制：每轮完成后需积累足够新数据才触发下一轮
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .backtester import BacktestConfig
from .data_manager import DataManager
from .optimizer import FlowEdgeOptimizer, OptimizationConfig
from .param_registry import ParamRegistry
from .validator import ParamValidator, ValidationThresholds

logger = logging.getLogger("flowedge.optimizer.scheduler")


@dataclass
class SchedulerConfig:
    """调度器配置"""
    # 检查间隔（秒）— 多久查一次样本够不够，默认 5 分钟
    check_interval_s: int = 300
    # 数据窗口
    lookback_days: int = 14             # 回看天数（取最近 N 天数据）
    min_samples: int = 20               # 首轮最小样本量（积累 20 条即触发）
    min_new_signals: int = 20           # 后续轮次需要的新信号数量
    # 优化配置
    n_trials: int = 100                 # Optuna 试验次数
    param_groups: list[str] = field(
        default_factory=lambda: ["weights", "signal_thresholds", "gate"]
    )
    optimization_mode: str = "single"   # "single" / "walk_forward"
    objective_metric: str = "sharpe_ratio"
    # 数据分割
    train_pct: float = 0.7
    # 自动应用
    auto_apply: bool = True             # 验证通过后自动应用
    # 回测配置
    backtest_config: BacktestConfig = field(default_factory=BacktestConfig)
    # 验证阈值
    validation_thresholds: ValidationThresholds = field(default_factory=ValidationThresholds)
    # 兼容旧配置（不再使用，保留避免报错）
    interval_days: int = 7


@dataclass
class SchedulerRun:
    """单次调度执行记录"""
    run_id: str
    started_at: str                     # ISO8601 UTC
    finished_at: Optional[str] = None
    status: str = "running"             # running / success / failed / skipped
    # 数据
    total_signals: int = 0
    train_size: int = 0
    test_size: int = 0
    # 优化结果
    optimization_result: Optional[dict] = None
    # 验证结果
    validation_passed: bool = False
    validation_score: float = 0.0
    validation_details: Optional[dict] = None
    # 应用
    applied: bool = False
    snapshot_name: Optional[str] = None
    # 失败原因
    failure_reason: Optional[str] = None
    elapsed_s: float = 0


class OptimizationScheduler:
    """
    样本驱动的自动优化调度器。

    核心逻辑：
      - 每 check_interval_s 秒检查一次样本量（零成本 count 查询）
      - 首轮：总样本 ≥ min_samples 时触发
      - 后续轮：需要 ≥ min_new_signals 条新信号才触发下一轮
      - 正在运行时跳过（防止并发）

    使用方式（手动触发）：
        scheduler = OptimizationScheduler(registry, data_manager, optimizer)
        run = scheduler.run_once()

    使用方式（后台自动）：
        await scheduler.start_background()  # 自动监控样本量
    """

    def __init__(
        self,
        registry: ParamRegistry,
        data_manager: DataManager,
        optimizer: FlowEdgeOptimizer,
        config: Optional[SchedulerConfig] = None,
    ):
        self._registry = registry
        self._data_manager = data_manager
        self._optimizer = optimizer
        self._config = config or SchedulerConfig()
        self._history: list[SchedulerRun] = []
        self._is_running = False
        self._background_task: Optional[asyncio.Task] = None
        # 样本驱动：记录上一轮完成时的样本数，用于判断是否积累了足够新数据
        self._last_run_sample_count: int = 0

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def history(self) -> list[SchedulerRun]:
        return self._history

    def run_once(self, config: Optional[SchedulerConfig] = None) -> SchedulerRun:
        """执行一次完整的"优化→验证→应用"流程"""
        cfg = config or self._config
        run_id = f"sched_{int(time.time())}"
        run = SchedulerRun(
            run_id=run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._is_running = True
        start = time.time()

        try:
            # Step 1: 加载数据
            now_ms = int(time.time() * 1000)
            min_time_ms = None
            if cfg.lookback_days > 0:
                min_time_ms = now_ms - int(cfg.lookback_days * 86400 * 1000)
            records = self._data_manager.load_all(require_1h=True, min_time_ms=min_time_ms)
            run.total_signals = len(records)

            if len(records) < cfg.min_samples:
                run.status = "skipped"
                run.failure_reason = f"样本不足: {len(records)} < {cfg.min_samples}"
                logger.warning(f"[Scheduler] {run.failure_reason}")
                return run

            # Step 2: 分割数据
            split = self._data_manager.time_split(
                records,
                train_pct=cfg.train_pct,
                val_pct=0,
                test_pct=1.0 - cfg.train_pct,
            )
            train_data = split.train
            test_data = split.test
            run.train_size = len(train_data)
            run.test_size = len(test_data)

            if len(train_data) < cfg.min_samples // 2:
                run.status = "skipped"
                run.failure_reason = f"训练集不足: {len(train_data)}"
                return run

            # Step 3: 执行优化（根据数据量自适应 n_trials）
            quality = self._data_manager.quality_check()
            date_range_days = float(getattr(quality, "date_range_days", 0))
            adaptive_n_trials = cfg.n_trials
            if date_range_days < 7:
                adaptive_n_trials = min(cfg.n_trials, 50)
                logger.info(f"[Scheduler] 数据跨度 {date_range_days:.1f} 天 < 7 天，降低 n_trials 至 {adaptive_n_trials}")

            logger.info(
                f"[Scheduler] 开始优化: {len(train_data)} 条训练数据, "
                f"{adaptive_n_trials} trials, mode={cfg.optimization_mode}"
            )

            opt_config = OptimizationConfig(
                mode=cfg.optimization_mode,
                n_trials=adaptive_n_trials,
                param_groups=cfg.param_groups,
                objective_metric=cfg.objective_metric,
                backtest_config=cfg.backtest_config,
            )
            try:
                opt_result = self._optimizer.run(train_data, opt_config)
            except RuntimeError as e:
                run.status = "skipped"
                run.failure_reason = str(e)
                logger.warning(f"[Scheduler] {run.failure_reason}")
                return run

            run.optimization_result = {
                "study_name": opt_result.study_name,
                "mode": opt_result.mode,
                "n_trials": opt_result.n_trials_completed,
                "best_value": opt_result.best_value,
                "elapsed_s": opt_result.elapsed_s,
            }

            if not opt_result.best_params:
                run.status = "failed"
                run.failure_reason = "优化未产出有效参数"
                return run

            # Step 4: 三层验证
            logger.info(f"[Scheduler] 开始验证: {len(test_data)} 条测试数据")

            validator = ParamValidator(
                registry=self._registry,
                thresholds=cfg.validation_thresholds,
            )
            val_result = validator.validate(
                params=opt_result.best_params,
                oos_data=test_data,
                train_metrics=opt_result.best_metrics,
                backtest_config=cfg.backtest_config,
            )

            run.validation_passed = val_result.passed
            run.validation_score = val_result.overall_score
            run.validation_details = {
                "layers": [
                    {
                        "layer": lr.layer,
                        "passed": lr.passed,
                        "score": lr.score,
                        "warnings": lr.warnings,
                    }
                    for lr in val_result.layers
                ],
                "recommendations": val_result.recommendations,
            }

            # Step 5: 应用或保留
            if val_result.passed and cfg.auto_apply:
                snapshot = self._registry.apply_and_save(
                    opt_result.best_params,
                    label=f"scheduler_{run_id}",
                )
                run.applied = True
                run.snapshot_name = snapshot
                run.status = "success"
                logger.info(f"[Scheduler] 验证通过，已应用参数，快照: {snapshot}")
            elif val_result.passed:
                run.status = "success"
                logger.info("[Scheduler] 验证通过，但 auto_apply=False，未自动应用")
            else:
                run.status = "failed"
                run.failure_reason = "验证未通过: " + "; ".join(
                    w for lr in val_result.layers for w in lr.warnings
                )
                logger.warning(f"[Scheduler] {run.failure_reason}")

        except Exception as e:
            run.status = "failed"
            run.failure_reason = f"异常: {str(e)}"
            logger.error(f"[Scheduler] 执行异常: {e}", exc_info=True)

        finally:
            run.elapsed_s = round(time.time() - start, 2)
            run.finished_at = datetime.now(timezone.utc).isoformat()
            self._history.append(run)
            self._is_running = False
            # 记录本轮完成时的样本数（用于下一轮冷却判断）
            if run.status != "skipped":
                self._last_run_sample_count = run.total_signals

        return run

    def _should_trigger(self) -> tuple[bool, str]:
        """
        检查是否应该触发优化。
        返回 (should_trigger, reason)。

        增强：检查数据时间跨度，太短的数据会导致严重过拟合。
        """
        if self._is_running:
            return False, "优化正在运行中"

        # 快速查询当前已验证信号总数
        try:
            quality = self._data_manager.quality_check()
            current_count = int(getattr(quality, "total_records", 0))
            records_with_1h = int(getattr(quality, "records_with_1h", 0))
            date_range_days = float(getattr(quality, "date_range_days", 0))
        except Exception as e:
            return False, f"数据查询异常: {e}"

        # 使用已验证（有 1h 结果）的记录数作为有效样本
        effective = records_with_1h

        # 首轮：从未成功运行过，检查总样本量
        if self._last_run_sample_count == 0:
            if effective >= self._config.min_samples:
                return True, f"首轮触发: {effective} 条已验证信号 >= {self._config.min_samples}"
            return False, f"等待首轮样本: {effective}/{self._config.min_samples} 条已验证信号"

        # 后续轮：检查新增信号量
        new_signals = effective - self._last_run_sample_count
        if new_signals >= self._config.min_new_signals:
            return True, f"新增 {new_signals} 条信号 >= {self._config.min_new_signals}，触发下一轮"
        return False, f"等待新数据: 新增 {new_signals}/{self._config.min_new_signals} 条"

    async def start_background(self, interval_hours: Optional[float] = None):
        """
        启动后台样本驱动调度。

        每 check_interval_s 秒检查一次样本量，达标即触发优化。
        interval_hours 参数保留兼容性但不再作为主要机制。
        """
        check_interval = self._config.check_interval_s

        if self._background_task and not self._background_task.done():
            logger.warning("[Scheduler] 后台调度已在运行")
            return

        async def _loop():
            while True:
                try:
                    should, reason = self._should_trigger()
                    if should:
                        logger.info(f"[Scheduler] 触发优化: {reason}")
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, self.run_once)
                    else:
                        logger.debug(f"[Scheduler] 跳过: {reason}")
                except Exception as e:
                    logger.error(f"[Scheduler] 后台调度异常: {e}")
                await asyncio.sleep(check_interval)

        self._background_task = asyncio.create_task(_loop())
        logger.info(
            f"[Scheduler] 后台调度已启动 — 样本驱动模式 "
            f"(每 {check_interval}s 检查, 首轮 >= {self._config.min_samples} 条, "
            f"后续每 {self._config.min_new_signals} 条新信号触发)"
        )

    async def stop_background(self):
        """停止后台调度"""
        if self._background_task:
            self._background_task.cancel()
            self._background_task = None
            logger.info("[Scheduler] 后台调度已停止")

    def get_status(self) -> dict:
        """获取调度器状态"""
        # 实时检查触发条件
        should_trigger, trigger_reason = self._should_trigger()

        status = {
            "is_running": self._is_running,
            "background_active": (
                self._background_task is not None
                and not self._background_task.done()
            ) if self._background_task else False,
            "total_runs": len(self._history),
            "trigger_mode": "sample_driven",
            "should_trigger": should_trigger,
            "trigger_reason": trigger_reason,
            "last_run_sample_count": self._last_run_sample_count,
            "config": {
                "check_interval_s": self._config.check_interval_s,
                "lookback_days": self._config.lookback_days,
                "min_samples": self._config.min_samples,
                "min_new_signals": self._config.min_new_signals,
                "n_trials": self._config.n_trials,
                "auto_apply": self._config.auto_apply,
            },
        }

        if self._history:
            last = self._history[-1]
            status["last_run"] = {
                "run_id": last.run_id,
                "status": last.status,
                "started_at": last.started_at,
                "elapsed_s": last.elapsed_s,
                "validation_passed": last.validation_passed,
                "applied": last.applied,
            }

        # 统计
        success = sum(1 for r in self._history if r.status == "success")
        failed = sum(1 for r in self._history if r.status == "failed")
        skipped = sum(1 for r in self._history if r.status == "skipped")
        status["stats"] = {
            "success": success,
            "failed": failed,
            "skipped": skipped,
        }

        return status

    def get_history(self, limit: int = 10) -> list[dict]:
        """获取调度历史"""
        runs = self._history[-limit:]
        return [
            {
                "run_id": r.run_id,
                "status": r.status,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "elapsed_s": r.elapsed_s,
                "total_signals": r.total_signals,
                "train_size": r.train_size,
                "test_size": r.test_size,
                "validation_passed": r.validation_passed,
                "validation_score": r.validation_score,
                "applied": r.applied,
                "snapshot_name": r.snapshot_name,
                "failure_reason": r.failure_reason,
            }
            for r in reversed(runs)
        ]
